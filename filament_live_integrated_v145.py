"""v14.5 single-window monochrome live measurement controls.

The MJPEG stream is for visualization and ROI interaction only. Each actual
measurement fetches a lossless 4608x2592 grayscale PNG from /snapshot.png.
"""
from __future__ import annotations
import io
import json
import queue
import threading
import time
import urllib.request
from pathlib import Path
from tkinter import ttk, messagebox
import tkinter as tk

import cv2
import numpy as np
from PIL import Image


class IntegratedLiveControls(ttk.LabelFrame):
    def __init__(self, app, parent):
        super().__init__(parent, text="LIVE CAMERA / 视频采集", padding=8)
        self.app = app
        self.url = tk.StringVar(value="http://127.0.0.1:8765")
        self.lens = tk.StringVar(value="9.515")
        self.exposure_us = tk.StringVar(value="1000")
        self.analogue_gain = tk.StringVar(value="1.0")
        self.interval_s = tk.StringVar(value="2.0")
        self.continuous = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Disconnected")
        self.info_text = tk.StringVar(value="Connect to start live preview in the main image area")
        self.frame_queue = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.worker_stop = threading.Event()
        self.native_size_wh = (4608,2592)
        self.last_pil = None
        self.session_id = None
        self.current_epoch = None
        self.background_epoch = None
        self.capture_inflight = False
        self._auto_after = None
        self._frame_times = []
        self._fps_display = 0.0
        self._last_fps_update = 0.0
        self.capture_dir = Path(__file__).resolve().parent / 'captures_live_v145'
        self._build()
        self.after(100,self._poll)

    def _build(self):
        self.columnconfigure(0, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky='ew')
        bar.columnconfigure(0,weight=1)
        ttk.Entry(bar, textvariable=self.url, width=23).grid(row=0,column=0,sticky='ew')
        ttk.Button(bar,text='Connect',command=self._connect).grid(row=0,column=1,padx=3)
        ttk.Button(bar,text='Stop',command=self._disconnect).grid(row=0,column=2)

        f = ttk.LabelFrame(self,text='Focus lock / 对焦',padding=4)
        f.grid(row=1,column=0,sticky='ew',pady=(6,0))
        ttk.Button(f,text='AF once',command=self._auto_focus).pack(side='left')
        ttk.Entry(f,textvariable=self.lens,width=8).pack(side='left',padx=4)
        ttk.Button(f,text='Set & Lock',command=self._manual_focus).pack(side='left')

        f = ttk.LabelFrame(self,text='Manual exposure / 手动曝光',padding=4)
        f.grid(row=2,column=0,sticky='ew',pady=(6,0))
        r=ttk.Frame(f);r.pack(fill='x')
        ttk.Label(r,text='µs:').pack(side='left')
        ttk.Entry(r,textvariable=self.exposure_us,width=8).pack(side='left',padx=3)
        ttk.Label(r,text='Gain:').pack(side='left')
        ttk.Entry(r,textvariable=self.analogue_gain,width=6).pack(side='left',padx=3)
        ttk.Button(r,text='Apply',command=self._manual_exposure).pack(side='left')
        r=ttk.Frame(f);r.pack(fill='x',pady=(4,0))
        for us,t in ((100,'0.1ms'),(500,'0.5ms'),(1000,'1ms'),(5000,'5ms')):
            ttk.Button(r,text=t,command=lambda v=us:self._preset_exposure(v)).pack(side='left',padx=1)

        ttk.Label(self,text='MONOCHROME 8-bit | White balance disabled',wraplength=305).grid(
            row=3,column=0,sticky='ew',pady=(6,0))

        f=ttk.LabelFrame(self,text='Full-res 4608×2592 measurements',padding=4)
        f.grid(row=4,column=0,sticky='ew',pady=(6,0))
        ttk.Button(f,text='Capture EMPTY background',command=lambda:self._capture('background')).pack(fill='x')
        ttk.Button(f,text='Refresh current frame for ROI',command=lambda:self._capture('current')).pack(fill='x',pady=3)
        ttk.Button(f,text='Capture + MEASURE 3 views',command=lambda:self._capture('measure')).pack(fill='x')
        r=ttk.Frame(f);r.pack(fill='x',pady=(5,0))
        ttk.Checkbutton(r,text='Continuous measure',variable=self.continuous,command=self._toggle_continuous).pack(side='left')
        ttk.Label(r,text='Interval (s):').pack(side='left',padx=(4,0))
        ttk.Entry(r,textvariable=self.interval_s,width=5).pack(side='left',padx=2)
        ttk.Label(f,text='Y-only live preview. Measurements: 4608×2592 gray8 lossless PNG, not preview pixels.',wraplength=300).pack(anchor='w',pady=(4,0))

        ttk.Label(self,textvariable=self.status_text,wraplength=320,justify='left').grid(row=5,column=0,sticky='ew',pady=(5,0))
        ttk.Label(self,textvariable=self.info_text,wraplength=320,justify='left').grid(row=6,column=0,sticky='ew')
        self.app._bind_controls_wheel_recursive(self)

    def _base_url(self):
        s=self.url.get().strip().rstrip('/')
        if not s.startswith(('http://','https://')):
            raise ValueError('URL must start with http:// or https://')
        return s

    @staticmethod
    def _request(base_url,endpoint,payload=None,timeout=20):
        data=None if payload is None else json.dumps(payload).encode('utf-8')
        req=urllib.request.Request(base_url+endpoint,data=data,
            method='POST' if data is not None else 'GET',
            headers={'Content-Type':'application/json'} if data is not None else {})
        with urllib.request.urlopen(req,timeout=timeout) as r:
            return r.read(),dict(r.headers)

    def _start_action(self,kind,endpoint,data=None,timeout=20):
        try:
            url=self._base_url()
        except ValueError as exc:
            self.info_text.set(str(exc));return
        def worker():
            try:
                body,headers=self._request(url,endpoint,data,timeout)
                self.events.put((kind,True,body,headers))
            except Exception as exc:
                self.events.put((kind,False,str(exc),{}))
        threading.Thread(target=worker,daemon=True).start()

    def _connect(self):
        self._disconnect()
        try:url=self._base_url()
        except ValueError as e:
            self.info_text.set(str(e));return
        self.status_text.set('Connecting...')
        self.worker_stop=threading.Event()
        stopped=self.worker_stop
        def run():
            try:
                with urllib.request.urlopen(url+'/stream.mjpg',timeout=8) as r:
                    self.events.put(('connected',True,b'',{}))
                    data=bytearray()
                    while not stopped.is_set():
                        block=r.read(8192)
                        if not block: raise ConnectionError('MJPEG stream ended')
                        data.extend(block)
                        while True:
                            start=data.find(b'\xff\xd8')
                            if start<0:
                                if len(data)>10_000_000:data.clear()
                                break
                            end=data.find(b'\xff\xd9',start+2)
                            if end<0:
                                if start>0:del data[:start]
                                break
                            jpeg=bytes(data[start:end+2]);del data[:end+2]
                            try:
                                frame=Image.open(io.BytesIO(jpeg)).convert('RGB')
                                frame=frame.transpose(Image.Transpose.ROTATE_270)
                            except Exception:continue
                            try:self.frame_queue.get_nowait()
                            except queue.Empty:pass
                            self.frame_queue.put_nowait(frame)
            except Exception as exc:
                if not stopped.is_set():self.events.put(('connection',False,str(exc),{}))
        threading.Thread(target=run,daemon=True).start()
        self._start_action('status','/status')

    def _disconnect(self):
        self.worker_stop.set()
        self.continuous.set(False)
        self.status_text.set('Disconnected')
        self.app._live_active=False

    def _manual_focus(self):
        try:v=float(self.lens.get())
        except ValueError:
            messagebox.showerror('Focus','LensPosition must be numeric',parent=self);return
        self._start_action('manual_focus','/focus_manual',{'lens_position':v})

    def _auto_focus(self):
        if messagebox.askyesno('Autofocus','AF will invalidate old background and may affect geometric calibration. Continue?',parent=self):
            self._start_action('auto_focus','/focus_once',{})

    def _manual_exposure(self):
        try:
            exposure=int(self.exposure_us.get());gain=float(self.analogue_gain.get())
            if not 50<=exposure<=100000 or not 1<=gain<=16:raise ValueError()
        except ValueError:
            messagebox.showerror('Exposure','Exposure 50–100000 µs; gain 1–16',parent=self);return
        self._start_action('manual_exposure','/exposure_manual',{'exposure_us':exposure,'analogue_gain':gain})

    def _preset_exposure(self,us):
        self.exposure_us.set(str(us));self._manual_exposure()

    def _capture(self,kind):
        if self.capture_inflight:
            self.info_text.set('Previous full-resolution frame still downloading');return
        self.capture_inflight=True
        self._start_action('capture:'+kind,'/snapshot.png',timeout=60)

    def _handle_capture(self,kind,blob,headers):
        if not blob.startswith(b'\x89PNG\r\n\x1a\n') or b'IEND' not in blob[-32:]:
            raise ValueError('Snapshot must be a complete grayscale PNG from v14.5 server')
        decoded=cv2.imdecode(np.frombuffer(blob,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
        if decoded is None or decoded.ndim != 2 or decoded.dtype != np.uint8:
            raise ValueError('Snapshot is not 8-bit single-channel grayscale')
        w,h=self.native_size_wh
        if decoded.shape != (h,w):
            raise ValueError(f'Snapshot {decoded.shape} is not true full-res {(h,w)}')
        session=headers.get('X-Session-Id',headers.get('X-Session-ID',''))
        epoch=(session,headers.get('X-Focus-Epoch',''),headers.get('X-Settings-Epoch','0'))
        self.current_epoch=epoch
        self.capture_dir.mkdir(parents=True,exist_ok=True)
        filename='background_reference.png' if kind=='background' else 'current_latest.png'
        path=self.capture_dir/filename
        path.write_bytes(blob)  # native-resolution lossless grayscale; rotate only for UI
        metadata={'focus_epoch':epoch[1], 'settings_epoch':epoch[2], 'session':session,
                  'exposure_us':headers.get('X-Exposure-Time-Us'),
                  'analogue_gain':headers.get('X-Analogue-Gain'),
                  'pixel_format':'GRAY8_PNG_LOSSLESS',
                  'native_size_wh':list(self.native_size_wh),
                  'lens_position':headers.get('X-Lens-Position')}
        path.with_suffix('.json').write_text(json.dumps(metadata,indent=2))
        if kind=='background':
            self.background_epoch=epoch
            self.app._load_background_from_live(path)
            self.info_text.set('Fresh full-resolution background acquired. Add filament and draw ROI.')
            return
        if self.background_epoch is not None and epoch!=self.background_epoch:
            self._invalidate_background('Focus or exposure changed; capture a NEW background')
        self.app._load_image(path)
        if kind=='measure':
            if self.background_epoch is None or self.app.background_cv is None:
                self.info_text.set('Cannot measure: capture EMPTY background after camera settings are fixed.')
            elif not all(self.app.views[k]['search_roi_xyxy'] for k in ('direct','left','right')):
                self.info_text.set('Cannot measure: draw Direct ROI and align mirrors first.')
            else:
                self.app._v14_process_all()
                self.info_text.set(f'Latest full-res frame measured | {time.strftime("%H:%M:%S")}')

    def _invalidate_background(self,msg):
        self.background_epoch=None
        self.continuous.set(False)
        self.app._clear_live_background()
        self.info_text.set(msg)

    def _toggle_continuous(self):
        if self._auto_after is not None:
            self.after_cancel(self._auto_after);self._auto_after=None
        if not self.continuous.get():return
        try:
            delay=float(self.interval_s.get())
            if not 0.5<=delay<=30:raise ValueError()
        except ValueError:
            self.continuous.set(False)
            messagebox.showerror('Interval','Enter 0.5–30 seconds',parent=self);return
        if self.background_epoch is None or self.app.background_cv is None:
            self.continuous.set(False)
            messagebox.showwarning('No background','Capture EMPTY background first',parent=self);return
        if not all(self.app.views[k]['search_roi_xyxy'] for k in ('direct','left','right')):
            self.continuous.set(False)
            messagebox.showwarning('No ROI','Draw Direct ROI and align mirrors first',parent=self);return
        self._schedule_auto(0)

    def _schedule_auto(self,ms=None):
        if not self.continuous.get():return
        if ms is None:ms=int(float(self.interval_s.get())*1000)
        self._auto_after=self.after(ms,self._auto_tick)

    def _auto_tick(self):
        self._auto_after=None
        if self.continuous.get() and not self.capture_inflight:self._capture('measure')
        elif self.continuous.get():self._schedule_auto(250)

    def _poll(self):
        if not self.winfo_exists():return
        try:
            try:
                frame=self.frame_queue.get_nowait()
            except queue.Empty:frame=None
            if frame is not None:
                self.last_pil=frame
                now=time.monotonic()
                self._frame_times=[x for x in self._frame_times if now-x<3]+[now]
                self.app._on_live_preview(frame,self.native_size_wh)
                if now-self._last_fps_update > 1.0:
                    n=len(self._frame_times)
                    self._fps_display=((n-1)/(self._frame_times[-1]-self._frame_times[0])) if n>=2 and self._frame_times[-1]>self._frame_times[0] else 0.0
                    self._last_fps_update=now
                    self.status_text.set(f'LIVE: {self._fps_display:.1f} preview FPS | '
                                         f'full-res {self.native_size_wh[0]}×{self.native_size_wh[1]} | '
                                         f'{self.exposure_us.get()} µs, gain {self.analogue_gain.get()}')
            for _ in range(12):
                try:kind,ok,payload,headers=self.events.get_nowait()
                except queue.Empty:break
                if kind.startswith('capture:'):
                    self.capture_inflight=False
                    if ok:
                        try:self._handle_capture(kind.split(':',1)[1],payload,headers)
                        except Exception as exc:self.info_text.set(f'Snapshot/measure error: {exc}')
                    else:self.info_text.set(f'Snapshot error: {payload}')
                    if self.continuous.get():self._schedule_auto()
                    continue
                if not ok:
                    self.status_text.set(f'{kind}: {payload}')
                    continue
                if kind=='connected':
                    self.app._live_active=True
                    self.status_text.set('Connected · live video on central canvas')
                    self._capture('current')  # automatic first frame for ROI selection
                    continue
                if kind in ('status','manual_focus','auto_focus','manual_exposure'):
                    data=json.loads(payload)
                    self.native_size_wh=tuple(data.get('original_image_size_wh',self.native_size_wh))
                    self.lens.set(f'{data["lens_position_commanded"]:.3f}')
                    if 'exposure_us' in data:
                        self.exposure_us.set(str(data['exposure_us']))
                        self.analogue_gain.set(f'{data["analogue_gain"]:.3f}')
                    self.session_id=data.get('session_id',self.session_id)
                    n=len(self._frame_times)
                    fps=round((n-1)/max(0.1,self._frame_times[-1]-self._frame_times[0]),1) if n>=2 else 0
                    self.status_text.set(f'LIVE | {fps} FPS display | {self.native_size_wh} native | '
                      f'Exp {data.get("exposure_us")}µs Gain {data.get("analogue_gain")} | '
                      f'Focus {data.get("lens_position_commanded"):.3f}')
                    if kind in ('manual_focus','auto_focus','manual_exposure'):
                        self._invalidate_background('Camera focus or exposure changed; capture new EMPTY background.')
        finally:
            if self.winfo_exists():self.after(100,self._poll)

    def close(self):
        self.continuous.set(False)
        self.worker_stop.set()
        if self._auto_after is not None:
            try:self.after_cancel(self._auto_after)
            except tk.TclError:pass

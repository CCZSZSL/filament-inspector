#!/usr/bin/env python3
"""v14.7.1 one-attempt fast measurement experiment. v14.7.0 untouched.

Full-frame calibration/seed/background and all prior tests remain unchanged.
Only CONTINUOUS measurement may use native-pixel-band ROI from the new Pi
endpoint. Existing detector always operates on full-sized, correctly rotated
cached arrays, with same per-pixel data in the 40 original columns.
"""
from __future__ import annotations
from filament_inspector.paths import DATA_ROOT
import csv
from datetime import datetime
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from filament_inspector.ui.dashboard import DashboardUI
from filament_inspector.web.server import DashboardStore, LanDashboardServer, network_addresses
from filament_inspector.acquisition.roi import choose_band, decode_validate_roi_png, install_band_in_place


class FilamentInspectorApp(DashboardUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.7.2 — FAST ROI + LAN Dashboard')
        self._web_store=DashboardStore(max_rows=1500)
        self.fast_enabled=tk.BooleanVar(value=False)
        self.fast_status=tk.StringVar(value='OFF: v14.7.0 full-resolution capture (safe baseline)')
        self.fast_interval=tk.StringVar(value='0')
        self.fast_metrics=[]
        self.fast_failed=False
        self.fast_log_path=None
        self._fast_pending_band=None
        lc=self.live_controls
        # Leave established camera/experiment paths intact; patch ONLY the continuous scheduler.
        self._fast_original_capture=lc._capture
        self._fast_original_handle=lc._handle_capture
        self._fast_original_schedule=lc._schedule_auto
        lc._capture=self._fast_capture
        lc._handle_capture=self._fast_handle_capture
        lc._schedule_auto=self._fast_schedule_auto
        base=self.adv_button.master
        for w in base.winfo_children():
            grid=w.grid_info()
            if grid and int(grid['row'])>=3:
                w.grid_configure(row=int(grid['row'])+1)
        panel=ttk.LabelFrame(base,text='v14.7.1 — High Speed ROI (optional)',padding=7)
        panel.grid(row=3,column=0,sticky='ew',pady=(0,7))
        check=ttk.Checkbutton(panel,text='Use FAST ROI for Continuous Measurement',variable=self.fast_enabled,command=self._fast_toggled)
        check.pack(anchor='w')
        ttk.Label(panel,text='Native 64-px band; original 40 columns, no resizing.\n'
                  'Full-frame background and calibration unchanged. Requires NEW Pi server.',
                  justify='left',wraplength=330).pack(anchor='w')
        ttk.Label(panel,textvariable=self.fast_status,wraplength=330,justify='left').pack(anchor='w',pady=(3,0))
        ttk.Button(panel,text='STOP FAST / USE STABLE FULL FRAME',command=self._fast_fallback).pack(fill='x',pady=(5,0))
        self._bind_controls_wheel_recursive(base)

    def _web_start(self):
        """Only the HTTP static directory changes; read-only API stays v14.7.0."""
        if self._web_server and self._web_server.running:return
        try:
            port=int(self.web_port.get().strip())
            server=LanDashboardServer(self._web_store,port=port)
            server.start()
        except (OSError,ValueError) as exc:
            self.web_detail.set('Cannot start HTTP listener: '+str(exc))
            messagebox.showerror('LAN Web Dashboard',str(exc));return
        self._web_server=server
        self._web_publish()
        addrs=network_addresses()
        urls='\n'.join(f'http://{ip}:{port}/' for ip in addrs) if addrs else f'http://<Mac-LAN-IP>:{port}/'
        self.web_address.set('LAN browser address:\n'+urls)
        self.web_detail.set('v14.7.2 · Read-only · Same trusted LAN')
        self.web_start_button.state(['disabled']);self.web_port_entry.state(['disabled'])
        for b in (self.web_stop_button,self.web_open_button,self.web_copy_button):b.state(['!disabled'])

    def _fast_toggled(self):
        if self.live_controls.continuous.get() or self._exp_job is not None:
            self.fast_enabled.set(not self.fast_enabled.get())
            messagebox.showwarning('Fast ROI','Stop measurement before changing the acquisition mode')
            return
        self.fast_failed=False
        self.fast_status.set('FAST ARM: run Continuous; full-frame calibration and experiments remain original' if self.fast_enabled.get() else 'OFF: v14.7.0 full-frame continuous measurement')

    def _fast_fallback(self):
        lc=self.live_controls
        if lc.continuous.get():
            self._toggle_continuous_button()
        self.fast_enabled.set(False)
        self.fast_status.set('FAST OFF. Restart Continuous to use original full-size PNG, or run v14.7.0.')

    def _toggle_continuous_button(self):
        starting=not self.live_controls.continuous.get()
        if starting and self.fast_enabled.get():
            self.fast_metrics=[]
            self.fast_failed=False
            self.fast_log_path=None
        return super()._toggle_continuous_button()

    def _fast_schedule_auto(self,ms=None):
        if self.fast_enabled.get() and self.live_controls.continuous.get():
            # No after-completion 0.5s pause; capture and processing remain serial.
            # Tk stays responsive; network transfers use existing worker thread.
            if self.live_controls._auto_after is not None:
                self.live_controls.after_cancel(self.live_controls._auto_after)
            self.live_controls._auto_after=self.live_controls.after(1 if ms is None else ms,self.live_controls._auto_tick)
            return
        return self._fast_original_schedule(ms)

    def _fast_capture(self,kind):
        lc=self.live_controls
        if kind!='measure' or not self.fast_enabled.get() or not lc.continuous.get():
            return self._fast_original_capture(kind)
        if lc.capture_inflight:return
        try:
            if self._exp_job is not None or self._calibration_job or self._seed_mode or self._seed_waiting_capture:
                raise ValueError('Finish experiments, seed learning and calibration before FAST')
            if lc.background_epoch is None or self.background_cv is None or self.image_cv is None:
                raise ValueError('Fresh full-size background and initial full-size current frame required')
            self._commit_fields()
            self._check_mirror_prereqs()
            if lc.native_size_wh!=(4608,2592):
                raise ValueError('FAST requires original 4608x2592 camera mode')
            # Local pixel columns are in WORKING 90-degree CW coordinates.
            x0,width=choose_band(self.views['left']['search_roi_xyxy'],self.views['right']['search_roi_xyxy'])
            self._fast_pending_band=(x0,width)
            url=lc._base_url()
            lc.capture_inflight=True
        except Exception as exc:
            return self._fast_fail(str(exc))
        def worker():
            try:
                start=time.perf_counter()
                body,headers=lc._request(url,f'/snapshot_roi.png?x0={x0}&width={width}',timeout=15)
                headers['X-Client-Request-Ms']=str((time.perf_counter()-start)*1000)
                lc.events.put(('capture:measure',True,body,headers))
            except Exception as exc:
                lc.events.put(('capture:measure',True,b'',{'X-Fast-Request-Error':str(exc)}))
                # Asynchronous failure must stop the fast measurement; Tk poll catches it.
        threading.Thread(target=worker,daemon=True).start()

    def _fast_handle_capture(self,kind,blob,headers):
        # A capture may finish AFTER the operator clicks STOP. Never feed its
        # partial ROI PNG to the original full-frame decoder.
        is_roi=('X-ROI-Working-X0' in headers or 'X-Fast-Request-Error' in headers)
        if kind != 'measure' or not is_roi:
            return self._fast_original_handle(kind,blob,headers)
        if not self.fast_enabled.get() or not self.live_controls.continuous.get():
            return  # safely discard a late ROI response after STOP
        t0=time.perf_counter()
        try:
            if headers.get('X-Fast-Request-Error'):
                raise RuntimeError(headers['X-Fast-Request-Error'])
            if self._fast_pending_band is None:
                raise ValueError('Missing requested ROI band')
            x0,width=self._fast_pending_band
            decoded, epoch=decode_validate_roi_png(blob,headers,x0,width,
                native_size_wh=self.live_controls.native_size_wh,
                expected_epoch=self.live_controls.background_epoch)
            # Reconfirm the 40-column detector band was not changed mid-download.
            x_now,width_now=choose_band(self.views['left']['search_roi_xyxy'],self.views['right']['search_roi_xyxy'])
            if (x_now,width_now)!=(x0,width):
                raise ValueError('Mirror ROI moved while a fast frame was in transit')
            install_band_in_place(self.image_cv,self.image_cv_raw,decoded,x0)
            lc=self.live_controls
            lc.current_epoch=epoch
            # The existing seed acquisition guard reads current_latest.json.
            # It gets the frame's actual reported camera settings without any full image writes.
            acq={'session':epoch[0],'focus_epoch':epoch[1], 'settings_epoch':epoch[2],
                 'exposure_us':headers.get('X-Exposure-Time-Us'),
                 'analogue_gain':headers.get('X-Analogue-Gain'),
                 'lens_position':headers.get('X-Lens-Position')}
            path=Path(self.image_path).with_suffix('.json')
            path.write_text(__import__('json').dumps(acq,indent=2))
            # Unmodified original processing / F-Direct / continuous CSV / Web publication.
            self._v14_process_all()
            self._record_continuous_result()
            dt_ms=(time.perf_counter()-t0)*1000
            now=time.monotonic()
            self.fast_metrics.append((now,dt_ms,float(headers.get('X-Client-Request-Ms','0'))))
            if len(self.fast_metrics)>1200:self.fast_metrics=self.fast_metrics[-1200:]
            fps=(len(self.fast_metrics)-1)/(self.fast_metrics[-1][0]-self.fast_metrics[0][0]) if len(self.fast_metrics)>1 and self.fast_metrics[-1][0]>self.fast_metrics[0][0] else 0
            self.fast_status.set(f'FAST ROI: {fps:.2f} effective FPS / {len(self.fast_metrics)} frames · HTTP {self.fast_metrics[-1][2]:.0f} ms · measure {dt_ms:.0f} ms')
            self._fast_log(headers,dt_ms, self.fast_metrics[-1][2],fps)
        except Exception as exc:
            self._fast_fail(str(exc))

    def _fast_log(self,headers,measure_ms,http_ms,fps):
        try:
            if self.fast_log_path is None:
                base=DATA_ROOT/'measurements_v1471';base.mkdir(exist_ok=True)
                self.fast_log_path=base/('fast_roi_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')
                with self.fast_log_path.open('w',newline='') as f:
                    csv.writer(f).writerow(['timestamp','frame_id','sensor_monotonic_ns','capture_ms','http_ms',
                          'detector_ms','fps_effective','left_mm','right_mm','f_direct_mm','delta_lr_mm','status'])
            f=self.f_direct_result or {}
            with self.fast_log_path.open('a',newline='') as out:
                csv.writer(out).writerow([datetime.now().astimezone().isoformat(),
                    headers.get('X-Fast-Frame-ID'),headers.get('X-Capture-Monotonic-Ns'),
                    headers.get('X-Capture-Elapsed-Ms'),f'{http_ms:.3f}',f'{measure_ms:.3f}',f'{fps:.3f}',
                    f.get('left_mm'),f.get('right_mm'),f.get('value'),f.get('absolute_difference_mm'),
                    'VALID_MM' if f.get('value') is not None else 'PIXEL_ONLY'])
        except OSError as exc:
            self.fast_status.set('FAST measurement OK, timing CSV error: '+str(exc))

    def _fast_fail(self,msg):
        self.fast_failed=True
        self.fast_enabled.set(False)
        if self.live_controls.continuous.get():
            self.live_controls.continuous.set(False)
            self.live_controls._toggle_continuous()  # Cancel pending callbacks.
        self.fast_status.set('FAST STOPPED: '+str(msg)[:180]+' · revert to v14.7.0')
        if hasattr(self,'continuous_status'):self.continuous_status.set('FAST STOPPED: '+str(msg)[:90])
        self.live_controls.info_text.set('Fast ROI halted: '+str(msg))
        try:
            dest=DATA_ROOT/'measurements_v1471';dest.mkdir(exist_ok=True)
            with (dest/'fast_roi_failures.log').open('a',encoding='utf-8') as f:
                f.write(datetime.now().astimezone().isoformat()+' | '+str(msg)+'\n')
        except OSError:pass
        return None

if __name__=='__main__':
    FilamentInspectorApp().mainloop()

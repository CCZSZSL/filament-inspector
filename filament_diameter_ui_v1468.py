#!/usr/bin/env python3
"""v14.6.8 — add two non-overlapping positional experiments to v14.6.7.

Detector, robust fusion, calibration, ROI tracking, seed learning, F-Direct and
Pi server remain v14.6.7 implementations. HTTP acquisition runs on a daemon
worker; Tk controls and result rendering run exclusively on the GUI thread.
"""
from __future__ import annotations
from datetime import datetime
import queue
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from filament_diameter_ui_v1467 import FilamentUIV1467
from filament_experiments_v1468 import ExperimentLog, new_row, extract_result, format_report

ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / 'measurements_v1468'


class FilamentUIV1468(FilamentUIV1467):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.6.8 | Position-Induced Error Analysis')
        self._exp_job = None
        self._exp_after = None
        self._exp_poll_id = None
        self._exp_queue = queue.Queue()
        self._exp_disabled_buttons = []
        self._exp_last_dir = None
        self.exp_interval = tk.StringVar(value='2.0')
        self.exp_progress = tk.StringVar(value='0 / 0')
        self.exp_status = tk.StringVar(value='IDLE · native 4608×2592 GRAY8 PNG')
        self.exp_location = tk.StringVar(value='No experiment saved')

        base = self.adv_button.master
        for w in base.winfo_children():
            grid = w.grid_info()
            if grid and int(grid['row']) >= 4:
                w.grid_configure(row=int(grid['row']) + 1)
        panel = ttk.LabelFrame(base, text='Measurement Experiments | v14.6.8', padding=8)
        panel.grid(row=4,column=0,sticky='ew',pady=(0,7))
        panel.columnconfigure(0,weight=1)
        self.exp_static_button = ttk.Button(panel, text='START STATIC TEST — 50 FRAMES',
                                            command=lambda:self._exp_start('static',50))
        self.exp_static_button.grid(row=0,column=0,sticky='ew',pady=2,ipady=4)
        self.exp_motion_button = ttk.Button(panel, text='START MOTION TEST — 100 FRAMES',
                                            command=lambda:self._exp_start('motion',100))
        self.exp_motion_button.grid(row=1,column=0,sticky='ew',pady=2,ipady=4)
        self.exp_stop_button = ttk.Button(panel, text='STOP TEST',command=self._exp_stop,state='disabled')
        self.exp_stop_button.grid(row=2,column=0,sticky='ew',pady=2,ipady=4)
        interval = ttk.Frame(panel);interval.grid(row=3,column=0,sticky='w',pady=(4,0))
        ttk.Label(interval,text='Interval after each frame (s):').pack(side='left')
        ttk.Entry(interval,textvariable=self.exp_interval,width=8).pack(side='left',padx=6)
        ttk.Label(interval,text='0.5–30').pack(side='left')
        self.exp_bar=ttk.Progressbar(panel,maximum=100,value=0)
        self.exp_bar.grid(row=4,column=0,sticky='ew',pady=(6,1))
        ttk.Label(panel,textvariable=self.exp_progress).grid(row=5,column=0,sticky='w')
        ttk.Label(panel,textvariable=self.exp_status,wraplength=335).grid(row=6,column=0,sticky='w')
        ttk.Label(panel,textvariable=self.exp_location,wraplength=335).grid(row=7,column=0,sticky='w')
        self.exp_view_button=ttk.Button(panel,text='VIEW TEST RESULTS',command=self._exp_view_results)
        self.exp_view_button.grid(row=8,column=0,sticky='ew',pady=(5,0))
        ttk.Label(panel,text='For uncorrected bias testing, first uncheck Y-Offset Compensation. '
                 'Then repeat with compensation enabled for comparison. Original measurement unchanged.',
                  wraplength=334).grid(row=9,column=0,sticky='w',pady=3)
        self._bind_controls_wheel_recursive(base)

    def _exp_start(self, name, target):
        if self._exp_job is not None:
            messagebox.showwarning('Experiment running','STOP the existing experiment first.');return
        lc=self.live_controls
        try:
            interval=float(self.exp_interval.get())
            if not 0.5 <= interval <= 30:
                raise ValueError('Interval must be 0.5–30 seconds')
            if self._calibration_job or self._seed_waiting_capture or self._seed_mode:
                raise ValueError('Finish Seed Learning and Calibration first')
            if lc.continuous.get() or lc.capture_inflight or lc._auto_after is not None:
                raise ValueError('Stop Continuous Measurement and wait for current capture')
            if self._exp_after is not None:
                raise ValueError('A previous experiment timer is still pending')
            if lc.background_epoch is None or self.background_cv is None:
                raise ValueError('Capture a new EMPTY background under locked camera settings')
            if tuple(lc.native_size_wh) != (4608,2592):
                raise ValueError('Camera native image must be 4608×2592')
            self._commit_fields()
            self._check_mirror_prereqs()
            base_url=lc._base_url()
            stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            dest=OUTPUT_ROOT/f'{stamp}_{name}_{target}frames'
            metadata={
                'schema':'filament_position_experiment_v1468','test':name,'target':target,
                'interval_seconds_after_completion':interval,
                'start_time':datetime.now().astimezone().isoformat(),
                'camera_server_url':base_url,'native_size_wh':list(lc.native_size_wh),
                'image_format':'GRAY8_PNG_LOSSLESS',
                'image_working_rotation_clockwise':90,
                'background_epoch':list(lc.background_epoch),
                'camera_settings_at_start':self._camera_settings(),
                'position_compensation_enabled':bool(self.position_enabled.get()),
                'views':{n:{'search_roi_xyxy':list(self.views[n]['search_roi_xyxy']),
                            'base_scale_calibration':bool(self.views[n].get('local_scale_calibration')),
                            'position_profile_present':bool(self.views[n].get('position_scale_profile'))}
                         for n in ('left','right')},
                'measurement':'Unmodified v14.6.7 detector/40-column fusion; F-Direct virtual mean',
                'image_retention':'Only current_latest.png is retained by unchanged camera client; full frames processed in memory.'
            }
            logfile=ExperimentLog(dest,name,target,metadata)
        except Exception as exc:
            messagebox.showerror('Cannot start experiment',str(exc));return
        self._exp_job={'name':name,'target':target,'interval':interval,
                       'base_url':base_url,'expected_epoch':tuple(lc.background_epoch),
                       'log':logfile,'attempts':0,'inflight':False,'stop':False}
        self._exp_last_dir=dest
        self.exp_location.set(str(dest))
        self.exp_progress.set(f'0 / {target} attempts')
        self.exp_bar.configure(maximum=target,value=0)
        self.exp_status.set('STARTING · capture uses original Camera Server /snapshot.png')
        self._exp_set_buttons(True)
        self._exp_next()

    def _exp_set_buttons(self, busy):
        if busy:
            self._exp_disabled_buttons=[]
            def walk(w):
                for child in w.winfo_children():
                    if isinstance(child,ttk.Button) and child not in (self.exp_stop_button,self.exp_view_button):
                        if 'disabled' not in child.state():
                            child.state(['disabled'])
                            self._exp_disabled_buttons.append(child)
                    walk(child)
            walk(self)
            self.exp_stop_button.state(['!disabled'])
        else:
            for button in self._exp_disabled_buttons:
                try:
                    if button.winfo_exists():button.state(['!disabled'])
                except tk.TclError:pass
            self._exp_disabled_buttons=[]
            self.exp_stop_button.state(['disabled'])

    def _exp_next(self):
        self._exp_after=None
        job=self._exp_job
        if job is None:return
        if job['stop'] or job['attempts'] >= job['target']:
            self._exp_finish();return
        lc=self.live_controls
        if lc.capture_inflight or lc.continuous.get() or self._calibration_job:
            self._exp_fail_remaining('Camera resource conflict; experiment aborted');return
        if lc.background_epoch is None or tuple(lc.background_epoch)!=job['expected_epoch']:
            self._exp_fail_remaining('Camera/background epoch changed; experiment aborted');return
        job['attempts']+=1
        attempt=job['attempts']
        job['inflight']=True
        lc.capture_inflight=True
        self.exp_status.set(f'{job["name"].upper()} · downloading frame {attempt}/{job["target"]}…')
        # No Tk use in background thread; same request function and URL as v14.6.7.
        def worker(url,attempt_number):
            try:
                blob,headers=lc._request(url,'/snapshot.png',timeout=60)
                self._exp_queue.put((attempt_number,True,blob,headers,''))
            except Exception as exc:
                self._exp_queue.put((attempt_number,False,None,{},str(exc)))
        threading.Thread(target=worker,args=(job['base_url'],attempt),daemon=True).start()
        self._exp_poll_id=self.after(80,self._exp_poll)

    def _exp_poll(self):
        self._exp_poll_id=None
        job=self._exp_job
        if not job:return
        try:
            attempt,ok,blob,headers,reason=self._exp_queue.get_nowait()
        except queue.Empty:
            self._exp_poll_id=self.after(80,self._exp_poll);return
        if not job['inflight'] or attempt!=job['attempts']:
            self._exp_fail_remaining('Unexpected experiment capture reply');return
        row=new_row(attempt,job['name'])
        try:
            if not ok:
                row['failure_reason']='Camera request failed: '+reason
            else:
                row['snapshot_bytes']=len(blob)
                row['camera_session']=headers.get('X-Session-Id',headers.get('X-Session-ID',''))
                row['focus_epoch']=headers.get('X-Focus-Epoch','')
                row['settings_epoch']=headers.get('X-Settings-Epoch','')
                row['exposure_us']=headers.get('X-Exposure-Time-Us','')
                # Prevent stale previous-frame measurements ever entering failure rows.
                self.view_results.clear()
                self._seed_failure_reasons.clear()
                self.f_direct_result=None
                # Inherited validator ensures complete native 4608×2592 GRAY8 PNG.
                # 'current' is the unmodified original camera receive/decode path.
                self._previous_capture_handler('current',blob,headers)
                row['capture_status']='OK'
                if self.live_controls.background_epoch is None or self.background_cv is None:
                    raise ValueError('Background invalidated after capture: epoch/settings mismatch')
                if tuple(self.live_controls.background_epoch)!=job['expected_epoch']:
                    raise ValueError('Camera metadata epoch mismatch; test must stop')
                # Same v14.6.7 processing: dynamic ROI, seed and 40-col robust fusion.
                self.view_results.clear()
                self.f_direct_result=None
                self._v14_process_all()
                extract_result(row,self.view_results,self.f_direct_result,self._seed_failure_reasons)
        except Exception as exc:
            row['failure_reason']=str(exc)
            if row['capture_status']=='OK':
                extract_result(row,self.view_results,self.f_direct_result,self._seed_failure_reasons)
                row['failure_reason']='Processing/validation error: '+str(exc)
                row['frame_status']='INVALID'
        finally:
            job['inflight']=False
            self.live_controls.capture_inflight=False
        try:
            job['log'].append(row)
        except Exception as exc:
            self.exp_status.set('CSV WRITE ERROR: '+str(exc))
            self._exp_finish(stopped=True)
            messagebox.showerror('Experiment CSV failure',str(exc))
            return
        count=job['attempts'];total=job['target']
        self.exp_progress.set(f'{count} / {total} attempts · valid mm: '
                              f'{sum(r["frame_status"]=="VALID_MM" for r in job["log"].rows)}')
        self.exp_bar.configure(value=count)
        if row['frame_status']!='VALID_MM':
            self.exp_status.set(f'Frame {count}: {row["frame_status"]} · {row["failure_reason"][:125]}')
        else:
            self.exp_status.set(f'{job["name"].upper()} · {count}/{total} · F-Direct={row["f_direct_mm"]:.5f} mm')
        if job['stop'] or count>=total:
            self._exp_finish(stopped=job['stop'])
        elif row['capture_status']=='OK' and self.live_controls.background_epoch is None:
            self._exp_finish(stopped=True)
        else:
            self._exp_after=self.after(round(job['interval']*1000),self._exp_next)

    def _exp_fail_remaining(self, reason):
        job=self._exp_job
        if job and job['attempts'] and job['inflight']:
            row=new_row(job['attempts'],job['name'])
            row['failure_reason']=reason
            try:job['log'].append(row)
            except OSError:pass
        self.exp_status.set(reason)
        self._exp_finish(stopped=True)

    def _exp_stop(self):
        job=self._exp_job
        if not job:return
        job['stop']=True
        if self._exp_after is not None:
            try:self.after_cancel(self._exp_after)
            except tk.TclError:pass
            self._exp_after=None
        if job['inflight']:
            self.exp_status.set('STOP REQUESTED · waiting for current in-flight snapshot to finish')
        else:self._exp_finish(stopped=True)

    def _exp_finish(self,stopped=None):
        job=self._exp_job
        if job is None:return
        if self._exp_after is not None:
            try:self.after_cancel(self._exp_after)
            except tk.TclError:pass
            self._exp_after=None
        if self._exp_poll_id is not None:
            try:self.after_cancel(self._exp_poll_id)
            except tk.TclError:pass
            self._exp_poll_id=None
        if stopped is None:stopped=job['stop'] or job['attempts']<job['target']
        self._exp_job=None
        self.live_controls.capture_inflight=False
        try:
            report=job['log'].finish(stopped=stopped)
            self.exp_status.set(('STOPPED' if stopped else 'COMPLETE')+
                                f' · {report["valid_mm_frames"]}/{report["attempted_frames"]} valid mm frames')
            self.exp_progress.set(f'{report["attempted_frames"]} / {job["target"]} attempts · '
                                  f'{report["valid_mm_frames"]} valid mm')
            self.exp_location.set(str(job['log'].csv_path))
        except Exception as exc:
            self.exp_status.set('REPORT WRITE ERROR: '+str(exc))
            self.exp_location.set(str(job['log'].csv_path))
        self._exp_set_buttons(False)

    def _exp_view_results(self):
        if self._exp_last_dir is None:
            messagebox.showinfo('No results','Run an experiment first.');return
        dest=Path(self._exp_last_dir)
        path=dest/'report.txt'
        if path.exists():text=path.read_text(encoding='utf-8')
        elif self._exp_job:
            from filament_experiments_v1468 import analyse
            job=self._exp_job
            text=format_report(analyse(job['log'].rows,job['name'],job['target'],stopped=True))
        else:text='Report not available. Check directory: '+str(dest)
        win=tk.Toplevel(self);win.title('v14.6.8 — Experiment Results');win.geometry('880x650')
        frame=ttk.Frame(win,padding=10);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=str(dest),wraplength=840).pack(anchor='w')
        scroll=ttk.Scrollbar(frame);scroll.pack(side='right',fill='y')
        area=tk.Text(frame,wrap='none',yscrollcommand=scroll.set,font=('Menlo',11))
        area.pack(fill='both',expand=True)
        scroll.configure(command=area.yview)
        area.insert('1.0',text);area.configure(state='disabled')

    def _toggle_continuous_button(self):
        if getattr(self,'_exp_job',None):
            messagebox.showwarning('Experiment running','STOP TEST before continuous measurement.');return
        return super()._toggle_continuous_button()

    def _toggle_mirror_continuous(self):
        if getattr(self,'_exp_job',None):
            self.live_controls.continuous.set(False);return
        return super()._toggle_mirror_continuous()

    def _close_app(self):
        if getattr(self,'_exp_job',None):
            job=self._exp_job
            # If closing mid-download, preserve the attempted frame as an explicit
            # interrupted failure. The network thread is daemon and never touches Tk.
            if job['inflight']:
                row=new_row(job['attempts'],job['name'])
                row['failure_reason']='App closed while snapshot acquisition was in flight'
                try:job['log'].append(row)
                except OSError:pass
                job['inflight']=False
            self._exp_finish(stopped=True)
        return super()._close_app()


if __name__ == '__main__':
    FilamentUIV1468().mainloop()

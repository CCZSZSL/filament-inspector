#!/usr/bin/env python3
"""v14.7.0 — original Tk calibration + optional read-only LAN web dashboard.

The entire v14.6.9 measurement path is inherited without modifications. The
browser does not issue camera, calibration or capture commands. Only the Tk
thread touches UI and measurement state; HTTP uses a published data copy.
"""
from __future__ import annotations
from dataclasses import asdict
import csv
import math
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser

from filament_inspector.ui.profile import ProfileUI
from filament_inspector.recording.experiments import extract_result, new_row
from filament_inspector.web.server import DashboardStore, LanDashboardServer, network_addresses, clean_number


class DashboardUI(ProfileUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.7.0 | Tk Calibration + LAN Web')
        self._web_store=DashboardStore(max_rows=1500)
        self._web_server=None
        self._web_extras={}
        self._web_after_id=None
        self.web_port=tk.StringVar(value='8765')
        self.web_address=tk.StringVar(value='Web dashboard stopped — start after calibration')
        self.web_detail=tk.StringVar(value='View-only LAN access · Pi Camera Server unchanged')
        base=self.adv_button.master
        for w in base.winfo_children():
            grid=w.grid_info()
            if grid and int(grid['row'])>=6:
                w.grid_configure(row=int(grid['row'])+1)
        panel=ttk.LabelFrame(base,text='LAN Web Dashboard | v14.7.0 (read-only)',padding=8)
        panel.grid(row=6,column=0,sticky='ew',pady=(0,7));panel.columnconfigure(0,weight=1)
        row=ttk.Frame(panel);row.grid(row=0,column=0,sticky='ew')
        ttk.Label(row,text='Port:').pack(side='left')
        self.web_port_entry=ttk.Entry(row,textvariable=self.web_port,width=7)
        self.web_port_entry.pack(side='left',padx=5)
        self.web_start_button=ttk.Button(row,text='START WEB DASHBOARD',command=self._web_start)
        self.web_start_button.pack(side='left',padx=3)
        self.web_stop_button=ttk.Button(row,text='STOP WEB',command=self._web_stop)
        self.web_stop_button.pack(side='left',padx=3)
        self.web_stop_button.state(['disabled'])
        links=ttk.Frame(panel);links.grid(row=1,column=0,sticky='ew',pady=(4,0))
        self.web_open_button=ttk.Button(links,text='OPEN IN MAC BROWSER',command=self._web_open)
        self.web_open_button.pack(side='left',padx=(0,5))
        self.web_copy_button=ttk.Button(links,text='COPY LAN URL',command=self._web_copy)
        self.web_copy_button.pack(side='left')
        self.web_open_button.state(['disabled']);self.web_copy_button.state(['disabled'])
        ttk.Label(panel,textvariable=self.web_address,wraplength=340,justify='left').grid(row=2,column=0,sticky='w',pady=(4,0))
        ttk.Label(panel,textvariable=self.web_detail,wraplength=340,justify='left').grid(row=3,column=0,sticky='w')
        ttk.Label(panel,text='Set up and calibrate here first. Mac hosts the page; phones/PCs on the same '
                  'trusted LAN visit http://MAC-IP:PORT. No camera/measurement controls on the web.',
                  wraplength=340,justify='left').grid(row=4,column=0,sticky='w',pady=(3,0))
        self._bind_controls_wheel_recursive(base)
        self._web_tick()

    def _web_url(self,local=False):
        if not self._web_server:return ''
        addr='127.0.0.1' if local else ((network_addresses() or ['<Mac-LAN-IP>'])[0])
        return f'http://{addr}:{self._web_server.port}/'

    def _web_start(self):
        if self._web_server and self._web_server.running:return
        try:
            port=int(self.web_port.get().strip())
            server=LanDashboardServer(self._web_store,port=port)
            server.start()
        except (OSError,ValueError) as exc:
            self.web_detail.set('Cannot start HTTP listener: '+str(exc))
            messagebox.showerror('LAN Web Dashboard',str(exc))
            return
        self._web_server=server
        self._web_publish()
        addrs=network_addresses()
        urls='\n'.join(f'http://{ip}:{port}/' for ip in addrs) if addrs else f'http://<Mac-LAN-IP>:{port}/'
        self.web_address.set('LAN browser address:\n'+urls)
        self.web_detail.set('RUNNING · Read-only · Same Wi-Fi/LAN · No password (trusted network only)')
        self.web_start_button.state(['disabled']);self.web_port_entry.state(['disabled'])
        for b in (self.web_stop_button,self.web_open_button,self.web_copy_button):b.state(['!disabled'])

    def _web_stop(self):
        if self._web_server:
            self._web_server.stop();self._web_server=None
        if hasattr(self,'web_address'):
            self.web_address.set('Web dashboard stopped')
            self.web_detail.set('Offline — camera measurement remains unchanged')
            self.web_start_button.state(['!disabled']);self.web_port_entry.state(['!disabled'])
            for b in (self.web_stop_button,self.web_open_button,self.web_copy_button):b.state(['disabled'])

    def _web_open(self):
        if self._web_server and self._web_server.running:
            webbrowser.open(self._web_url(local=True))

    def _web_copy(self):
        if self._web_server and self._web_server.running:
            self.clipboard_clear();self.clipboard_append(self._web_url())
            self.web_detail.set('LAN URL copied. The other device must be on the same reachable local network.')

    def _exp_set_buttons(self,busy):
        super()._exp_set_buttons(busy)
        # No capture controls are exposed remotely, so opening a read-only view
        # during acquisition is safe. Match v14.6.9 treatment of dashboard.
        if busy and hasattr(self,'web_open_button'):
            for w in (self.web_open_button,self.web_copy_button,self.web_stop_button):
                if self._web_server and self._web_server.running:w.state(['!disabled'])
            if not (self._web_server and self._web_server.running):
                self.web_start_button.state(['!disabled'])
        if not busy and hasattr(self,'web_open_button'):
            if self._web_server and self._web_server.running:
                self.web_start_button.state(['disabled']);self.web_port_entry.state(['disabled'])
                for w in (self.web_open_button,self.web_copy_button,self.web_stop_button):w.state(['!disabled'])
            else:
                self.web_start_button.state(['!disabled']);self.web_port_entry.state(['!disabled'])
                for w in (self.web_open_button,self.web_copy_button,self.web_stop_button):w.state(['disabled'])

    def _v14_process_all(self):
        before=id(self.profile_recorder.samples[-1]) if getattr(self,'profile_recorder',None) and self.profile_recorder.samples else None
        result=super()._v14_process_all()  # algorithms and original Tk update unchanged
        if not hasattr(self,'_web_store') or not self.profile_recorder.samples:return result
        last=self.profile_recorder.samples[-1]
        if id(last)!=before:
            row=new_row(len(self.profile_recorder.samples),last.source,timestamp=last.timestamp)
            try:
                extract_result(row,self.view_results,self.f_direct_result,self._seed_failure_reasons)
                extra={k:row.get(k) for k in ('left_y_px','right_y_px','left_pixel_width','right_pixel_width',
                      'left_quality','right_quality','left_valid_columns','right_valid_columns',
                      'left_calibration_status','right_calibration_status','left_position_mode',
                      'right_position_mode','failure_reason')}
                for view in ('left','right'):
                    engine=(self.engines or {}).get(view)
                    f=((self.view_results or {}).get(view) or {}).get('fusion') or {}
                    if engine is not None and hasattr(engine,'widths_px'):
                        raw=getattr(engine,'widths_px',[])
                        valid=getattr(engine,'valid',[])
                        extra['edge_columns_'+view]=[
                            clean_number(raw[i]) if i<len(valid) and bool(valid[i]) else None
                            for i in range(min(40,len(raw)))]
                        # Keep raw widths for even the rejected fusion columns;
                        # separate accepted mask makes exclusion inspectable.
                        extra['edge_accepted_'+view]=f.get('accepted_indices',[])
                self._web_extras[id(last)]=extra
                # Bound memory independently of the 5k ProfileRecorder.
                if len(self._web_extras)>2000:
                    active={id(x) for x in self.profile_recorder.samples[-1550:]}
                    self._web_extras={k:v for k,v in self._web_extras.items() if k in active}
            except Exception as exc:
                self.web_detail.set('Web data extraction issue (detector unaffected): '+str(exc))
            self._web_publish()
        return result

    def _profile_clear(self):
        super()._profile_clear()
        if hasattr(self,'_web_extras'):self._web_extras.clear();self._web_publish()

    def _profile_load(self):
        # Preserve historical CSV diagnostics in the network view. The original
        # 14.6.9 recorder still owns parsing and keeps imported positions
        # unqualified for 3D (axial_mm=None).
        if self.live_controls.continuous.get() or self._exp_job is not None:
            messagebox.showwarning('Acquiring','Stop continuous measurement/experiment before loading CSV')
            return
        path=filedialog.askopenfilename(title='Load v14.6.8 experiment or continuous frames CSV',
                    filetypes=[('CSV files','*.csv'),('All files','*.*')])
        if not path:return
        try:
            with open(path,newline='',encoding='utf-8-sig') as src:
                csvrows=list(csv.DictReader(src))
            n=self.profile_recorder.from_csv(path)
        except Exception as exc:
            messagebox.showerror('CSV import failed',str(exc));return
        self._web_extras.clear()
        fields=('left_y_px','right_y_px','left_pixel_width','right_pixel_width',
                'left_valid_columns','right_valid_columns','left_quality','right_quality',
                'left_calibration_status','right_calibration_status',
                'left_position_mode','right_position_mode','failure_reason')
        for sample,original in zip(self.profile_recorder.samples,csvrows[-len(self.profile_recorder.samples):]):
            self._web_extras[id(sample)]={k:original[k] for k in fields if k in original}
        self.profile_status.set(f'Loaded {n} CSV rows · time plots only · no valid axial positions')
        self._profile_render();self._web_publish()

    def _profile_feed_toggled(self):
        before=id(self.profile_recorder.samples[-1]) if self.profile_recorder.samples else None
        super()._profile_feed_toggled()
        after=id(self.profile_recorder.samples[-1]) if self.profile_recorder.samples else None
        if hasattr(self,'_web_extras'):
            if before is not None and after is None:self._web_extras.clear()
            self._web_publish()

    def _toggle_continuous_button(self):
        old=self.live_controls.continuous.get()
        result=super()._toggle_continuous_button()
        if hasattr(self,'_web_store'):
            if not old and self.live_controls.continuous.get():self._web_extras.clear()
            self._web_publish()
        return result

    def _exp_start(self,name,target):
        before=self._exp_job
        result=super()._exp_start(name,target)
        if before is None and self._exp_job is not None and hasattr(self,'_web_extras'):
            self._web_extras.clear();self._web_publish()
        return result

    def _exp_poll(self):
        job=getattr(self,'_exp_job',None)
        count=len(job['log'].rows) if job else 0
        result=super()._exp_poll()
        if job and len(job['log'].rows)>count:
            logrow=job['log'].rows[-1]
            # Capture failures never call the detector. Explicitly record them
            # in web history instead of silently disappearing from the charts.
            if logrow['capture_status']!='OK' and hasattr(self,'profile_recorder'):
                from datetime import datetime
                sample=self.profile_recorder.append(at_s=time.monotonic(),
                    timestamp=logrow['timestamp'],source=job['name'],left_mm=None,
                    right_mm=None,f_direct_mm=None,delta_lr_mm=None,status='CAPTURE_FAILED',
                    feed_enabled=False)
                self._web_extras[id(sample)]={'failure_reason':logrow.get('failure_reason','')}
        if hasattr(self,'_web_store'):self._web_publish()
        return result

    def _exp_finish(self,stopped=None):
        result=super()._exp_finish(stopped=stopped)
        if hasattr(self,'_web_store'):self._web_publish()
        return result

    def _web_tick(self):
        if hasattr(self,'_web_store'):
            try:self._web_publish()
            except Exception as exc:
                if hasattr(self,'web_detail'):self.web_detail.set('Web status issue: '+str(exc))
            self._web_after_id=self.after(1100,self._web_tick)

    def _web_publish(self):
        if not hasattr(self,'_web_store'):return
        record=self.profile_recorder
        samples=record.samples[-1500:]
        rows=[]
        for sample in samples:
            r=asdict(sample)
            extra=self._web_extras.get(id(sample),{})
            r.update(extra)
            # No client should infer roundness/roughness from the visual model.
            a=clean_number(r.get('left_mm'));b=clean_number(r.get('right_mm'))
            r['signed_lr_mm']=a-b if a is not None and b is not None else None
            rows.append(r)
        job=getattr(self,'_exp_job',None)
        exp={'name':job['name'],'attempts':job['attempts'], 'target':job['target'],
             'progress':self.exp_progress.get(),'status':self.exp_status.get()} if job else {}
        mode='experiment' if job else ('continuous' if self.live_controls.continuous.get() else 'idle')
        self._web_store.publish(measurements=rows,mode=mode,
            calibration=(self.cal_state.get() if hasattr(self,'cal_state') else 'Tk calibration'),
            experiment=exp)

    def _close_app(self):
        if getattr(self,'_web_after_id',None) is not None:
            try:self.after_cancel(self._web_after_id)
            except tk.TclError:pass
            self._web_after_id=None
        self._web_stop()
        return super()._close_app()

if __name__=='__main__':
    DashboardUI().mainloop()

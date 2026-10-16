#!/usr/bin/env python3
"""v14.6.9 — visual dashboard, no changes to v14.6.8 measurements.

New dashboard presents fused diameter, per-mirror disagreement, and an optional
AXISYMMETRIC visualization of a real axial-feed run. It is NOT roughness/Ra,
roundness, real 3D microtopography, or any new estimator.
"""
from __future__ import annotations
from datetime import datetime
import math
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from filament_diameter_ui_v1468 import FilamentUIV1468
from filament_profile_v1469 import ProfileRecorder, radius_mesh_sections, number


class FilamentUIV1469(FilamentUIV1468):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.6.9 | Live Diameter Dashboard')
        self.profile_recorder=ProfileRecorder()
        self.profile_speed=tk.StringVar(value='20.0')
        self.profile_feed=tk.BooleanVar(value=False)
        self.profile_status=tk.StringVar(value='No measurements · open dashboard for live plots')
        self.profile_magnify=tk.StringVar(value='20')
        self.profile_window=None
        self.profile_canvas_d=None
        self.profile_canvas_lr=None
        self.profile_canvas_3d=None
        self.profile_titles={}
        base=self.adv_button.master
        for widget in base.winfo_children():
            g=widget.grid_info()
            if g and int(g['row'])>=5:
                widget.grid_configure(row=int(g['row'])+1)
        frame=ttk.LabelFrame(base,text='Live Diameter Dashboard | v14.6.9',padding=8)
        frame.grid(row=5,column=0,sticky='ew',pady=(0,7));frame.columnconfigure(0,weight=1)
        self.profile_open_button=ttk.Button(frame,text='OPEN DASHBOARD — DIAMETER + ΔLR + 3D PROFILE',
                   command=self._profile_open)
        self.profile_open_button.grid(row=0,column=0,sticky='ew',ipady=5)
        rr=ttk.Frame(frame);rr.grid(row=1,column=0,sticky='w',pady=3)
        ttk.Checkbutton(rr,text='Actual AXIAL feed active',variable=self.profile_feed,
                 command=self._profile_feed_toggled).pack(side='left')
        ttk.Label(rr,text='Speed (mm/s):').pack(side='left',padx=(9,2))
        self.profile_speed_entry=ttk.Entry(rr,textvariable=self.profile_speed,width=7)
        self.profile_speed_entry.pack(side='left')
        ttk.Label(frame,textvariable=self.profile_status,wraplength=334).grid(row=2,column=0,sticky='w')
        ttk.Label(frame,text='Only enable AXIAL feed during actual continuous material transport. '
                  'Static and lateral-motion experiments are time plots ONLY. 3D is an axisymmetric '
                  'diameter model, NOT physical surface roughness.',wraplength=334,
                  justify='left').grid(row=3,column=0,sticky='w',pady=(2,0))
        self._bind_controls_wheel_recursive(base)

    def _exp_set_buttons(self,busy):
        super()._exp_set_buttons(busy)
        # Dashboard is read-only and safe to open during a running experiment.
        if busy and hasattr(self,'profile_open_button'):
            self.profile_open_button.state(['!disabled'])

    def _profile_feed_toggled(self):
        if self.profile_feed.get():
            v=number(self.profile_speed.get())
            if v is None or v<=0:
                self.profile_feed.set(False)
                messagebox.showerror('Feed speed','Enter an actual positive axial feed speed in mm/s')
                return
        if self.profile_feed.get():self.profile_speed_entry.state(['disabled'])
        else:self.profile_speed_entry.state(['!disabled'])
        self.profile_recorder.reset()  # Never join two runs at same axial coordinate.
        self.profile_status.set('Fresh axial run required · measurements cleared')
        self._profile_render()

    def _toggle_continuous_button(self):
        old=self.live_controls.continuous.get()
        result=super()._toggle_continuous_button()
        new=self.live_controls.continuous.get()
        if not old and new and hasattr(self,'profile_recorder'):
            self.profile_recorder.reset()
            self._profile_render()
        return result

    def _exp_start(self,name,target):
        old=self._exp_job
        result=super()._exp_start(name,target)
        if old is None and self._exp_job is not None:
            self.profile_recorder.reset()
            self.profile_status.set(f'{name.upper()} test: time plots only · no axial 3D')
            self._profile_render()
        return result

    def _v14_process_all(self):
        # Inherited algorithm executes FIRST and unaltered. All data extraction
        # and drawing run afterward on the existing Tk main thread.
        result=super()._v14_process_all()
        if not hasattr(self,'profile_recorder'):
            return result
        if getattr(self,'_calibration_job',None):return result
        measurements=self.view_results or {}
        if not any(measurements.get(n) for n in ('left','right')):return result
        f=self.f_direct_result or {}
        left=number(f.get('left_mm'))
        right=number(f.get('right_mm'))
        fused=number(f.get('value'))
        delta=number(f.get('absolute_difference_mm'))
        source=('continuous' if self.live_controls.continuous.get() else
                (self._exp_job['name'] if self._exp_job is not None else 'manual'))
        feed=self.profile_feed.get() and source=='continuous'
        speed=number(self.profile_speed.get())
        if feed and (speed is None or speed<=0):
            # Never let a dashboard setting break diameter measurement.
            feed=False
            self.profile_status.set('Invalid axial speed · diameter unchanged, model paused')
        try:
            sample=self.profile_recorder.append(
                at_s=time.monotonic(), timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
                source=source,left_mm=left,right_mm=right, f_direct_mm=fused,delta_lr_mm=delta,
                status='VALID_MM' if fused is not None else 'PIXEL_ONLY / INVALID',
                feed_enabled=feed,speed_mm_s=speed)
            val=f'{fused:.5f} mm' if fused is not None else 'Pixel Only'
            dv=f'{delta*1000:.2f} µm' if delta is not None else '—'
            self.profile_status.set(f'F-Direct {val} · ΔLR {dv} · '
                                    f'{len(self.profile_recorder.samples)} samples · {source}')
            self._profile_render()
        except Exception as exc:
            self.profile_status.set('Dashboard issue (diameter algorithm unaffected): '+str(exc))
        return result

    def _profile_open(self):
        if self.profile_window is not None and self.profile_window.winfo_exists():
            self.profile_window.lift();self.profile_window.focus_force();return
        window=tk.Toplevel(self);self.profile_window=window
        window.title('v14.6.9 — Diameter / Mirror Agreement / Axisymmetric Profile')
        window.geometry('1050x860');window.minsize(710,640)
        outer=ttk.Frame(window,padding=9);outer.pack(fill='both',expand=True)
        top=ttk.Frame(outer);top.pack(fill='x',pady=(0,5))
        ttk.Label(top,text='DIAMETER INSPECTION',font=('Helvetica',16,'bold')).pack(side='left')
        ttk.Button(top,text='Load CSV',command=self._profile_load).pack(side='right',padx=4)
        ttk.Button(top,text='Save Dashboard CSV',command=self._profile_save).pack(side='right',padx=4)
        ttk.Button(top,text='Clear history',command=self._profile_clear).pack(side='right',padx=4)
        ttk.Label(outer,textvariable=self.profile_status).pack(anchor='w')
        notebook=ttk.Notebook(outer);notebook.pack(fill='both',expand=True,pady=6)
        tab1=ttk.Frame(notebook,padding=8);tab2=ttk.Frame(notebook,padding=8)
        notebook.add(tab1,text=' Live trends ');notebook.add(tab2,text=' Axisymmetric 3D ')
        self.profile_canvas_d=tk.Canvas(tab1,bg='#101923',highlightthickness=0,height=285)
        self.profile_canvas_d.pack(fill='both',expand=True,pady=(0,8))
        self.profile_canvas_lr=tk.Canvas(tab1,bg='#101923',highlightthickness=0,height=225)
        self.profile_canvas_lr.pack(fill='both',expand=True)
        ttk.Label(tab1,text='F-Direct = (Left + Right)/2   |   ΔLR = |Left - Right|. '
              'ΔLR is NOT roundness. Missing fused-mm frames are shown as gaps.',
              wraplength=970).pack(anchor='w',pady=5)
        settings=ttk.Frame(tab2);settings.pack(fill='x',pady=4)
        ttk.Label(settings,text='Radial deviation exaggeration (visual only):').pack(side='left')
        selector=ttk.Combobox(settings,state='readonly',values=('1','5','10','20','50','100'),
                               textvariable=self.profile_magnify,width=6)
        selector.pack(side='left',padx=6)
        selector.bind('<<ComboboxSelected>>',lambda event:self._profile_render())
        self.profile_canvas_3d=tk.Canvas(tab2,bg='#101923',highlightthickness=0,height=375)
        self.profile_canvas_3d.pack(fill='both',expand=True,pady=9)
        ttk.Label(tab2,text='AXISYMMETRIC / REVOLVED DIAMETER PROFILE ONLY. '
          'Not a reconstruction of scratches, micro-texture, true roughness Ra/Rz, '
          'or roundness. Axial distance is approximated from host processing time and assumed feed speed; no slip or jitter corrections. '
          'No surface is drawn for invalid frames or independent test runs.',
          wraplength=980,justify='left').pack(anchor='w',pady=5)
        for c in (self.profile_canvas_d,self.profile_canvas_lr,self.profile_canvas_3d):
            c.bind('<Configure>',lambda event:self._profile_render())
        window.protocol('WM_DELETE_WINDOW',self._profile_close)
        self._profile_render()

    def _profile_close(self):
        if self.profile_window is not None:
            self.profile_window.destroy()
        self.profile_window=None
        self.profile_canvas_d=None
        self.profile_canvas_lr=None
        self.profile_canvas_3d=None

    def _profile_clear(self):
        self.profile_recorder.reset()
        self.profile_status.set('Dashboard history cleared · raw experiment/continuous CSV files unaffected')
        self._profile_render()

    def _profile_load(self):
        if self.live_controls.continuous.get() or self._exp_job is not None:
            messagebox.showwarning('Acquiring','Stop continuous measurement/experiment before loading CSV')
            return
        path=filedialog.askopenfilename(title='Load v14.6.8 experiment or continuous frames CSV',
                    filetypes=[('CSV files','*.csv'),('All files','*.*')])
        if not path:return
        try:
            n=self.profile_recorder.from_csv(path)
        except Exception as exc:
            messagebox.showerror('CSV import failed',str(exc));return
        self.profile_status.set(f'Loaded {n} CSV rows · time plots only · no valid axial positions')
        self._profile_render()

    def _profile_save(self):
        if not self.profile_recorder.samples:
            messagebox.showinfo('Nothing to save','No dashboard data yet');return
        path=filedialog.asksaveasfilename(title='Export dashboard measurements',defaultextension='.csv',
                    initialfile='diameter_dashboard_v1469.csv',filetypes=[('CSV file','*.csv')])
        if not path:return
        try:self.profile_recorder.save_csv(path)
        except Exception as exc:messagebox.showerror('CSV save error',str(exc))

    @staticmethod
    def _line_plot(canvas, samples, series, title, unit, *, floor_zero=False):
        if canvas is None:return
        canvas.delete('all')
        w=max(canvas.winfo_width(),460);h=max(canvas.winfo_height(),170)
        pad_l,pad_r,pad_t,pad_b=75,25,35,40
        x0,x1=pad_l,w-pad_r;y0,y1=pad_t,h-pad_b
        canvas.create_text(14,13,text=title,anchor='nw',fill='#f6f7fb',font=('Helvetica',12,'bold'))
        if not samples:
            canvas.create_text(w/2,h/2,text='Waiting for measurements',fill='#a8b7c8');return
        # Draw the latest 800 samples WITHOUT skipping invalid samples:
        # sub-sampling across them could falsely connect discontinuities.
        samples=samples[-800:]
        t0=samples[0].elapsed_s;t1=samples[-1].elapsed_s
        if t1<=t0:t1=t0+1
        ys=[getattr(s,key) for key,_ in series for s in samples if getattr(s,key) is not None]
        if not ys:
            canvas.create_text(w/2,h/2,text='No valid calibrated mm data',fill='#a8b7c8');return
        lo=min(ys);hi=max(ys)
        if floor_zero:lo=0
        diff=hi-lo
        if diff<0.001:mid=(hi+lo)/2;lo=mid-0.001;hi=mid+0.001
        else:
            margin=diff*.12
            if not floor_zero:lo-=margin
            hi+=margin
        def point(t,v):return (x0+(t-t0)/(t1-t0)*(x1-x0),y1-(v-lo)/(hi-lo)*(y1-y0))
        for i in range(5):
            val=lo+(hi-lo)*i/4;y=point(t0,val)[1]
            canvas.create_line(x0,y,x1,y,fill='#2c3b4d')
            canvas.create_text(x0-7,y,text=f'{val:.4f}',fill='#adbdce',anchor='e',font=('Menlo',9))
        canvas.create_line(x0,y1,x1,y1,fill='#6a7a8b')
        canvas.create_text(x0,y1+16,text=f'{t0:.1f}s',fill='#adbdce',anchor='w')
        canvas.create_text(x1,y1+16,text=f'{t1:.1f}s',fill='#adbdce',anchor='e')
        for key,color in series:
            coords=[]
            for s in samples:
                val=getattr(s,key)
                # Break on missing mm; do not interpolate invalid frames.
                if val is None:
                    if len(coords)>=4:canvas.create_line(*coords,fill=color,width=2,smooth=False)
                    coords=[];continue
                coords.extend(point(s.elapsed_s,val))
            if len(coords)>=4:canvas.create_line(*coords,fill=color,width=2,smooth=False)
            elif len(coords)==2:
                xx,yy=coords;canvas.create_oval(xx-2,yy-2,xx+2,yy+2,fill=color,outline='')
        cx=x1-5
        for key,color in reversed(series):
            canvas.create_text(cx,15,text=key.replace('_mm','').replace('f_direct','F-Direct').replace('delta_lr','ΔLR'),
                            anchor='ne',fill=color,font=('Helvetica',10,'bold'))
            cx-=({'f_direct_mm':85,'left_mm':55,'right_mm':60,'delta_lr_mm':50}.get(key,75))
        canvas.create_text(x0+3,19,text=unit,fill='#adbdce',anchor='nw')

    @staticmethod
    def _draw_3d(canvas, samples, exaggeration):
        if canvas is None:return
        canvas.delete('all')
        w=max(canvas.winfo_width(),520);h=max(canvas.winfo_height(),250)
        canvas.create_text(18,16,text='AXISYMMETRIC DIAMETER MODEL (NOT SURFACE ROUGHNESS)',
                           fill='#f4f7fc',font=('Helvetica',12,'bold'),anchor='nw')
        segments,baseline=radius_mesh_sections(samples,exaggeration=exaggeration)
        valid=[p for seg in segments for p in seg]
        if len(valid)<2:
            canvas.create_text(w/2,h/2,text='Start Continuous Measurement with AXIAL feed enabled.\n'
                               'At least two valid F-Direct samples are required.\n'
                               'Static / lateral-motion experiments never create a 3D length.',
                               fill='#bdccdc',justify='center')
            return
        zmin=min(p[0] for p in valid);zmax=max(p[0] for p in valid)
        if zmax<=zmin:
            canvas.create_text(w/2,h/2,text='No axial movement measured yet',fill='#bdccdc');return
        midy=h*.55;left=42;right=w-44
        # Normalize radial display to pixels. Uses exaggeration only for drawing.
        radius_max=max(p[1] for p in valid)
        def xy(z,rad,a):
            x=left+(z-zmin)/(zmax-zmin)*(right-left)
            rr=max(5,rad/max(radius_max,0.1)*min(78,h*.22))
            return (x+rr*.22*math.cos(a),midy+rr*math.sin(a))
        # Upper/lower outline and 3D elliptical cross-section ribs.
        for seg in segments:
            if len(seg)==1:continue
            upper=[xy(z,r,-math.pi/2) for z,r,_ in seg]
            lower=[xy(z,r, math.pi/2) for z,r,_ in seg]
            points=[x for q in (upper+lower[::-1]) for x in q]
            canvas.create_polygon(*points,fill='#286b91',outline='#70b9ea',width=1)
            for i in range(1,len(seg)):
                z,r,_=seg[i]
                if i==len(seg)-1 or i%max(1,len(seg)//15)==0:
                    p1=xy(z,r,-math.pi/2);p2=xy(z,r,math.pi/2)
                    canvas.create_line(*p1,*p2,fill='#97d5ed',width=1,dash=(3,4))
        run=next(s.axial_run for s in reversed(samples) if s.axial_run is not None)
        valid_positions=[s.axial_mm for s in samples if s.axial_run==run and s.axial_mm is not None]
        intervals=[b-a for a,b in zip(valid_positions,valid_positions[1:]) if b>a]
        from statistics import median
        pitch=f'{median(intervals):.2f} mm' if intervals else '—'
        canvas.create_text(20,h-75,text=f'Estimated median sampling pitch: {pitch}  (unmeasured material lies between samples)',
                           fill='#a9b9c9',anchor='nw')
        canvas.create_text(20,h-53,text=f'Approximate axial span: {zmax-zmin:.2f} mm   |   '
                    f'Mean measured diameter: {baseline:.5f} mm   |   Visual deviation ×{exaggeration}',
                    fill='#d4e4f3',anchor='nw')
        canvas.create_text(20,h-31,text='Invalid mm samples remain gaps. Circular cross-sections & intervening shape are assumptions.',
                           fill='#ffcf8e',anchor='nw')

    def _profile_render(self):
        try:
            samples=self.profile_recorder.samples
            self._line_plot(self.profile_canvas_d,samples,
              [('left_mm','#ffbd77'),('right_mm','#9ccaff'),('f_direct_mm','#7decaf')],
              'DIAMETER TREND — Left / Right / F-Direct','mm')
            self._line_plot(self.profile_canvas_lr,samples,
              [('delta_lr_mm','#ff989e')],
              'INTER-VIEW DIAMETER DIFFERENCE — |Left − Right|','mm',floor_zero=True)
            self._draw_3d(self.profile_canvas_3d,samples,int(self.profile_magnify.get()))
        except Exception as exc:
            # UI is observational: it never blocks or changes detector output.
            if hasattr(self,'profile_status'):
                self.profile_status.set('Dashboard render issue (detector unaffected): '+str(exc))


if __name__ == '__main__':
    FilamentUIV1469().mainloop()

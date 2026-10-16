#!/usr/bin/env python3
"""v14.6.6: simplified two-mirror GUI and safe flexible 10-frame calibration.

Preserves the v14.6.3-F measurement, v14.6.3 fusion, v14.6.2 seed and Pi server.
Physical Direct View is not measured, calibrated, drawn, or configured.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
from filament_diameter_ui_v1463_f_direct import FDirectTwoMirrorUI
from filament_dynamic_fusion_v1463 import measure_dynamic
from filament_centered_roi_v146 import centered_roi, fixed_x_band
from filament_three_view_core_v13 import VIEW_LABELS, new_view
from filament_diameter_paper_method_ui_v13_three_view import PALETTE
from filament_flexible_calibration_v1466 import (
    CalibrationError, FRAME_COUNT, MIN_GOOD_FRAMES, check_reference,
    frame_record, make_scale, validate_scale,
)

ROOT=Path(__file__).resolve().parent
AUTOSAVE=ROOT/'config'/'dual_mirror_v1466.json'
FIELDS=('polarity','search_roi_xyxy','expected_width_mm','width_range_mm',
        'approx_expected_width_px','foreground_threshold','edge_padding_px',
        'outer_edge_constraint','outer_edge_gate_radius_px','seed_gray_model',
        'local_scale_calibration')


def _plain(v):
    if isinstance(v,np.ndarray):return v.tolist()
    if isinstance(v,np.generic):return v.item()
    if isinstance(v,Path):return str(v)
    raise TypeError(f'Not JSON serializable: {type(v).__name__}')


def validate_saved_state(obj, image_size_wh=None):
    """Validate both views transactionally before changing GUI state."""
    if obj.get('schema')!='filament_dual_mirror_v1466':
        raise ValueError('Not a v14.6.6 dual-mirror setup JSON')
    if obj.get('working_rotation_degrees_clockwise')!=90:
        raise ValueError('Saved setup must use 90-degree CW working coordinates')
    size=obj.get('original_image_size_wh')
    if not isinstance(size,list) or len(size)!=2 or any(int(k)<=0 for k in size):
        raise ValueError('Saved native image size is invalid')
    if image_size_wh is not None and list(image_size_wh)!=size:
        raise ValueError(f'Image resolution mismatch: setup {size}, current {image_size_wh}')
    result={}
    rois=[]
    for n in ('left','right'):
        source=obj.get('views',{}).get(n)
        if not isinstance(source,dict):raise ValueError(f'Missing saved {n} view')
        view=new_view(n)
        for field in FIELDS:
            if field in source:view[field]=deepcopy(source[field])
        roi=view['search_roi_xyxy']
        if roi is not None:
            if not isinstance(roi,list) or len(roi)!=4 or not all(isinstance(x,int) for x in roi):
                raise ValueError(f'Invalid {n} ROI coordinates')
            x0,y0,x1,y1=roi
            if x1-x0+1!=40 or not(0<=x0<x1<size[1] and 0<=y0<y1<size[0]):
                # size stores native (4608,2592), working width is native height
                raise ValueError(f'{n} ROI invalid for 90-degree rotated camera')
            rois.append(roi)
        scale=view['local_scale_calibration']
        if scale is not None:
            validate_scale(scale,view_name=n,image_size_wh=size)
            if roi is None:raise ValueError(f'{n}: calibration without ROI')
        seed=view['seed_gray_model']
        if seed is not None and (not isinstance(seed,dict) or not isinstance(seed.get('samples'),list)):
            raise ValueError(f'Invalid {n} seed model')
        result[n]=view
    if len(rois)==2 and (rois[0][0],rois[0][2])!=(rois[1][0],rois[1][2]):
        raise ValueError('Left and Right must share 40-column X band')
    check_reference(obj.get('reference_diameter_mm'))
    return result


class FilamentUIV1466(FDirectTwoMirrorUI):
    def _build_ui(self):
        # Parent still initializes all legacy widgets / event bindings for
        # compatibility. Completely hide their interfaces, not their algorithms.
        super()._build_ui()
        self.title('Filament Inspector v14.6.6 | Dual Mirror + Flexible Calibration')
        self.geometry('1500x930')
        self._selected_name='left'
        self.selected_name_var.set(VIEW_LABELS['left'])
        self.views['direct']['search_roi_xyxy']=None
        self.views['direct']['profile']=None
        self.views['direct']['local_scale_calibration']=None
        self.views['direct']['seed_gray_model']=None
        self._roi_draw_name=None
        self._calibration_job=None
        self._calibration_watch_id=None
        self._startup_state=None
        self._loading_state=False
        self._auto_save_id=None
        self.cal_reference=tk.StringVar(value='1.75000')
        self.cal_state=tk.StringVar(value='Select a reference diameter; independent working-scale calibration.')
        self.cal_left=tk.StringVar(value='Left: Pixel Only — uncalibrated')
        self.cal_right=tk.StringVar(value='Right: Pixel Only — uncalibrated')
        self.delta_lr=tk.StringVar(value='ΔLR: — (not a roundness measurement)')
        self.live_controls.capture_dir=ROOT/'captures_live_v1466'
        self.live_controls.lens.set('9.750')  # default display; Pi status remains authoritative

        base=self.adv_button.master
        for w in base.winfo_children():w.grid_remove() # keep legacy widgets for inherited callbacks
        base.columnconfigure(0,weight=1)
        ttk.Label(base,text='Filament Inspector  v14.6.6',font=('Helvetica',17,'bold')).grid(
            row=0,column=0,sticky='w',pady=(0,4))
        ttk.Label(base,text='Dual Mirror · Full-Res GRAY8 · 40-column fusion · Virtual F-Direct',
                  wraplength=340).grid(row=1,column=0,sticky='w',pady=(0,6))

        self.live_controls.grid(row=2,column=0,sticky='ew',pady=(0,7))
        self._relabel_camera_capture_and_continuous()
        self._clean_central_labels()

        roi_box=ttk.LabelFrame(base,text='Mirror ROIs / 40-pixel fixed X',padding=7)
        roi_box.grid(row=3,column=0,sticky='ew',pady=(0,7));roi_box.columnconfigure(0,weight=1)
        xr=ttk.Frame(roi_box);xr.grid(row=0,column=0,sticky='ew')
        ttk.Label(xr,text='Shared X center:').pack(side='left')
        ttk.Entry(xr,textvariable=self.fixed_center_x_var,width=7).pack(side='left',padx=4)
        ttk.Button(xr,text='Apply X',command=self._apply_mirror_x).pack(side='left')
        buttons=ttk.Frame(roi_box);buttons.grid(row=1,column=0,sticky='ew',pady=(5,0))
        for i,(name,title) in enumerate((('left','Draw Left ROI'),('right','Draw Right ROI'))):
            buttons.columnconfigure(i,weight=1)
            ttk.Button(buttons,text=title,command=lambda v=name:self._start_mirror_draw(v)).grid(
                row=0,column=i,sticky='ew',padx=2)
        ttk.Button(roi_box,text='Pan / Move Image',command=self._enable_pan).grid(row=2,column=0,sticky='ew',pady=(4,0))
        ttk.Label(roi_box,text='Draw each Y band independently. Both mirror ROIs share the same 40-pixel X band. '
                                  'Drag a mirror box vertically to reposition it.',wraplength=332).grid(
            row=3,column=0,sticky='w',pady=(3,0))

        seed_box=ttk.LabelFrame(base,text='One-time Seed Learning',padding=7)
        seed_box.grid(row=4,column=0,sticky='ew',pady=(0,7));seed_box.columnconfigure(0,weight=1)
        self.mirror_choose=ttk.Combobox(seed_box,textvariable=self.selected_name_var,
            state='readonly',values=(VIEW_LABELS['left'],VIEW_LABELS['right']))
        self.mirror_choose.grid(row=0,column=0,sticky='ew')
        self.mirror_choose.bind('<<ComboboxSelected>>',self._selected_changed)
        buttons=ttk.Frame(seed_box);buttons.grid(row=1,column=0,sticky='ew',pady=(4,0))
        buttons.columnconfigure(0,weight=1);buttons.columnconfigure(1,weight=1)
        self.seed_button=ttk.Button(buttons,text='Learn Seed (NEW PNG)',command=self._start_seed_selection)
        self.seed_button.grid(row=0,column=0,sticky='ew',padx=2)
        ttk.Button(buttons,text='Clear Seed',command=self._clear_seed_model).grid(row=0,column=1,sticky='ew',padx=2)
        ttk.Label(seed_box,textvariable=self.seed_note,wraplength=330,justify='left').grid(
            row=2,column=0,sticky='w',pady=(4,0))

        cal=ttk.LabelFrame(base,text='Calibration Panel / Flexible Working Calibration',padding=8)
        cal.grid(row=5,column=0,sticky='ew',pady=(0,7));cal.columnconfigure(0,weight=1)
        rr=ttk.Frame(cal);rr.grid(row=0,column=0,sticky='ew')
        ttk.Label(rr,text='Reference Diameter (mm):').pack(side='left')
        ttk.Entry(rr,textvariable=self.cal_reference,width=11).pack(side='left',padx=5)
        bb=ttk.Frame(cal);bb.grid(row=1,column=0,sticky='ew',pady=(5,2))
        bb.columnconfigure(0,weight=1);bb.columnconfigure(1,weight=1)
        for i,(title,names) in enumerate((('Calibrate Left Mirror',('left',)),
                                           ('Calibrate Right Mirror',('right',)))):
            ttk.Button(bb,text=title,command=lambda v=names:self._begin_calibration(v)).grid(
                row=0,column=i,sticky='ew',padx=1)
        ttk.Button(bb,text='Calibrate Both Mirrors',
                   command=lambda:self._begin_calibration(('left','right'))).grid(
                row=1,column=0,columnspan=2,sticky='ew',pady=(4,0),padx=1)
        ttk.Label(cal,textvariable=self.cal_left,wraplength=340,justify='left').grid(row=2,column=0,sticky='w',pady=(4,0))
        ttk.Label(cal,textvariable=self.cal_right,wraplength=340,justify='left').grid(row=3,column=0,sticky='w')
        ttk.Label(cal,textvariable=self.cal_state,wraplength=340,justify='left').grid(row=4,column=0,sticky='w',pady=(3,0))
        ttk.Label(cal,text='10 NEW full-resolution frames/view; at least 8 inliers. '
                           'Nominal filament = Working Calibration, not ±0.01 mm validation.',
                  wraplength=340).grid(row=5,column=0,sticky='w',pady=(3,0))

        ttk.Button(base,text='MEASURE LEFT + RIGHT  →  F-DIRECT',command=self._v14_process_all).grid(
            row=6,column=0,sticky='ew',ipady=6,pady=(2,3))
        ttk.Label(base,textvariable=self.v14_hint,wraplength=344,justify='left').grid(
            row=7,column=0,sticky='w')
        ttk.Label(base,textvariable=self.delta_lr,wraplength=344).grid(row=8,column=0,sticky='w',pady=(4,0))

        diag=ttk.LabelFrame(base,text='Mirror diagnostics / Results',padding=7)
        diag.grid(row=9,column=0,sticky='ew',pady=(6,6));diag.columnconfigure(0,weight=1)
        ttk.Button(diag,text='40-column Slice Diagnostic',command=self._selected_diagnostic).grid(row=0,column=0,sticky='ew')
        ttk.Button(diag,text='Seed / Failure Diagnostic',command=self._show_seed_diagnostic).grid(row=1,column=0,sticky='ew',pady=(4,0))
        rw=ttk.Frame(base);rw.grid(row=10,column=0,sticky='ew',pady=(0,5))
        rw.columnconfigure(0,weight=1);rw.columnconfigure(1,weight=1)
        ttk.Button(rw,text='Save Setup JSON',command=self._save_setup_dialog).grid(row=0,column=0,sticky='ew',padx=2)
        ttk.Button(rw,text='Load Setup JSON',command=self._load_setup_dialog).grid(row=0,column=1,sticky='ew',padx=2)

        self.adv_button=ttk.Button(base,text='▶ More / Measurement settings',command=self._toggle_advanced)
        self.adv_button.grid(row=11,column=0,sticky='ew')
        adv=ttk.LabelFrame(base,text='Per-view detector parameters',padding=7)
        self.advanced_panel=adv
        adv.grid(row=12,column=0,sticky='ew',pady=4);adv.grid_remove()
        self.v14_advanced_open=False
        for r,(lbl,var) in enumerate((('Polarity',self.polarity_var),
                                      ('Approx. diameter (px)',self.approx_px_var),
                                      ('Background threshold',self.foreground_var),
                                      ('ROI edge padding',self.padding_var))):
            ttk.Label(adv,text=lbl).grid(row=r,column=0,sticky='w')
            if r==0:w=ttk.Combobox(adv,textvariable=var,state='readonly',values=('bright','dark'),width=12)
            else:w=ttk.Entry(adv,textvariable=var,width=12)
            w.grid(row=r,column=1,sticky='e')
        ttk.Button(adv,text='Apply Detector Parameters',command=self._apply_view_params).grid(
            row=4,column=0,columnspan=2,sticky='ew')
        ttk.Button(adv,text='Export Measurement JSON',command=self._export_results).grid(
            row=5,column=0,columnspan=2,sticky='ew',pady=3)
        ttk.Button(adv,text='Export Column CSV',command=self._export_csv).grid(
            row=6,column=0,columnspan=2,sticky='ew')

        table=self.result_table
        table.configure(displaycolumns=('view','median','valid','confidence'))
        for name,title,size in (('view','View',125),('median','Fused Diameter',140),
                                ('valid','Valid Columns',105),('confidence','Quality / Status',120)):
            table.heading(name,text=title);table.column(name,width=size,stretch=True)
        table.bind('<<TreeviewSelect>>',self._result_selected)
        self._show_f_direct_waiting('Pixel Only / Waiting')
        self.v14_hint.set('Set Left / Right ROIs; learn ONE seed per mirror. Capture EMPTY background, then measure.')
        self._refresh_fields();self._update_cal_labels()
        self._bind_controls_wheel_recursive(base)
        self._load_startup_state()
        self.protocol('WM_DELETE_WINDOW',self._close_app)
        self.after(5000,self._periodic_autosave)

    def _clean_central_labels(self):
        # Legacy central-canvas captions were not hidden with the left panel.
        def visit(parent):
            for child in parent.winfo_children():
                if isinstance(child,ttk.LabelFrame) and 'Three-view results' in str(child.cget('text')):
                    child.configure(text='Two-Mirror Results — F-Direct is a calculated average')
                if isinstance(child,ttk.Label):
                    old=str(child.cget('text'))
                    if 'Dual-mirror Inspection | v14.6.3-F' in old:
                        child.configure(text='Dual Mirror Inspection | v14.6.6 | F-Direct calculated')
                    elif 'Load a Direct View calibration profile' in old:
                        child.configure(textvariable='',text='Draw two mirror ROIs and learn a seed for each view. The physical Direct View is disabled.')
                    elif 'Physical mm appears only with view-specific H and K_ref' in old:
                        child.configure(textvariable='',text='Quality is a heuristic, not a confidence probability. mm requires independent mirror working scales; ±0.01 mm unverified.')
                visit(child)
        visit(self.canvas.master)

    def _relabel_camera_capture_and_continuous(self):
        lc=self.live_controls
        for w in lc.winfo_children():
            stack=[w]
            while stack:
                child=stack.pop();stack.extend(child.winfo_children())
                if isinstance(child,ttk.Button) and child.cget('text')=='Capture + MEASURE 3 views':
                    child.configure(text='Capture + MEASURE 2 mirrors')
                if isinstance(child,ttk.Checkbutton) and 'Continuous measure' in child.cget('text'):
                    child.configure(command=self._toggle_mirror_continuous)
        # Keep protocol/worker/stream/snapshot handling of v14.5 untouched.
        self._previous_capture_handler=lc._handle_capture
        lc._handle_capture=self._handle_mirror_capture

    def _toggle_mirror_continuous(self):
        lc=self.live_controls
        if lc._auto_after is not None:
            lc.after_cancel(lc._auto_after);lc._auto_after=None
        if not lc.continuous.get():return
        try:
            v=float(lc.interval_s.get())
            if not 0.5<=v<=30:raise ValueError('Enter interval 0.5–30 s')
            if self._calibration_job or self._seed_waiting_capture or self._seed_mode:
                raise ValueError('Finish calibration / seed selection before continuous measurement')
            self._check_mirror_prereqs()
            if lc.background_epoch is None or self.background_cv is None:
                raise ValueError('Capture an EMPTY live background first')
        except Exception as exc:
            lc.continuous.set(False);messagebox.showwarning('Cannot start Continuous Measure',str(exc));return
        lc._schedule_auto(0)

    def _handle_mirror_capture(self,kind,blob,headers):
        if kind=='calibration':
            # Parent 'current' performs all Pi metadata, PNG completeness,
            # native size, GRAY8 and epoch checks without measuring Direct.
            try:
                self._previous_capture_handler('current',blob,headers)
                self._accept_calibration_snapshot(headers)
            except Exception as exc:
                self._abort_calibration(str(exc))
            return
        if kind=='measure':
            self._previous_capture_handler('current',blob,headers)
            # No 3-ROI gating. Same freshly downloaded full-res PNG for L+R.
            if self.live_controls.background_epoch is None or self.background_cv is None:
                self.live_controls.info_text.set('Capture a NEW EMPTY background at locked camera settings')
                return
            self._v14_process_all()
            self.live_controls.info_text.set('Latest full-res frame: 2 mirrors + virtual F-Direct')
            return
        self._previous_capture_handler(kind,blob,headers) # 'seed','background','current'

    def _check_mirror_prereqs(self, names=('left','right')):
        self._require_photos()
        for n in names:
            v=self.views[n];roi=v.get('search_roi_xyxy')
            if roi is None:raise ValueError(f'Set {n} mirror ROI first')
            if not v.get('seed_gray_model'):raise ValueError(f'Learn {n} mirror seed first')
            if int(roi[2])-int(roi[0])+1!=40:raise ValueError(f'{n} ROI must be 40 pixels wide')
        if len(names)>1:
            a,b=[self.views[k]['search_roi_xyxy'] for k in ('left','right')]
            if (a[0],a[2])!=(b[0],b[2]):raise ValueError('Mirror ROIs must share fixed X')

    def _v14_process_all(self):
        if self._calibration_job:
            messagebox.showwarning('Calibration busy','Finish the 10-frame calibration first.');return
        try:self._check_mirror_prereqs()
        except Exception as exc:
            messagebox.showwarning('Measurement setup',str(exc));return
        self.process_all()
        f=self.f_direct_result
        if f:
            self.delta_lr.set(f'ΔLR = {f["absolute_difference_mm"]:.5f} mm  |  NOT roundness')
            self.v14_hint.set(f'F-Direct = {f["value"]:.5f} mm  (L {f["left_mm"]:.5f}, R {f["right_mm"]:.5f}) '
                                '— working calibration only, ±0.01 mm unverified')
        else:
            self.delta_lr.set('ΔLR: — (requires two valid calibrated mirror results)')
            reasons=' | '.join(self._seed_failure_reasons.get(n,'') for n in ('left','right')
                               if self._seed_failure_reasons.get(n))
            self.v14_hint.set('Pixel Only / F-Direct unavailable. '+reasons[:240])
        self._update_cal_labels()

    def _process_one(self,n):
        if n=='direct':return # physical Direct is completely disabled
        view=self.views[n];cal=view.get('local_scale_calibration')
        # Retain old scale in memory, but suppress use outside valid geometry
        # or after focus change; parent v14.6.3 still gates tracked-Y ±20px.
        temporary_disable=False
        if cal is not None:
            old=cal.get('search_roi_xyxy_at_calibration',[])
            roi=view.get('search_roi_xyxy') or []
            if len(old)!=4 or len(roi)!=4 or old[0]!=roi[0] or old[2]!=roi[2]:
                temporary_disable=True
            meta=self._seed_acquisition() or {}
            camera=cal.get('camera_settings') or {}
            old_focus=camera.get('lens_position')
            new_focus=meta.get('lens_position')
            if old_focus not in (None,'') and new_focus not in (None,''):
                try:
                    if abs(float(old_focus)-float(new_focus))>0.01:temporary_disable=True
                except (ValueError,TypeError):temporary_disable=True
        if temporary_disable:view['local_scale_calibration']=None
        try:return super()._process_one(n)
        finally:
            if temporary_disable:view['local_scale_calibration']=cal

    def _update_f_direct(self):
        result=super()._update_f_direct()
        if result:
            vals=list(self.result_table.item('direct','values'))
            vals[7]='FUSED mm'
            self.result_table.item('direct',values=vals)
        else:
            vals=list(self.result_table.item('direct','values'))
            vals[7]='Pixel Only / —'
            self.result_table.item('direct',values=vals)
        return result

    def _show_f_direct_waiting(self,state):
        if hasattr(self,'result_table'):
            self.result_table.item('direct',values=['F-Direct View',state,'VIRTUAL','—','—','—','—',state])

    def _result_selected(self,event=None):
        chosen=self.result_table.selection()
        if not chosen:return
        if chosen[0]=='direct':
            self.result_table.selection_remove('direct');return
        self._table_selected(event)

    def _start_mirror_draw(self,n):
        if self.image_cv is None:
            messagebox.showwarning('No image','Load/capture a full-resolution image first.');return
        if self._calibration_job or self._seed_waiting_capture or self._seed_mode:
            messagebox.showwarning('Busy','Finish sample selection / calibration first.');return
        self._commit_fields()
        self._selected_name=n
        self.selected_name_var.set(VIEW_LABELS[n])
        self._refresh_fields()
        self._roi_draw_name=n
        self.v14_mode='draw_mirror';self.tool_mode.set('point')
        self.canvas.configure(cursor='crosshair')
        self.v14_hint.set(f'Drag vertical Y bounds for {VIEW_LABELS[n]}; X stays locked to 40 pixels.')

    def _enable_pan(self):
        self._roi_draw_name=None
        return super()._enable_pan()

    def _v14_press(self,event):
        if self.v14_mode!='draw_mirror':return super()._v14_press(event)
        p=self._canvas_to_image(event.x,event.y)
        if p is None:return
        try:x0,x1=fixed_x_band(self.image_cv.shape,40,self._center_x())
        except ValueError as exc:
            messagebox.showwarning('X center',str(exc));return
        self.v14_drag={'which':'draw_mirror','anchor':p,'view':self._roi_draw_name}
        self.pending_box=((x0,p[1]),(x1,p[1]));self._draw_pending(*self.pending_box)

    def _v14_drag_motion(self,event):
        if not self.v14_drag or self.v14_drag.get('which')!='draw_mirror':
            return super()._v14_drag_motion(event)
        p=self._canvas_to_image(event.x,event.y)
        if p is None:return
        x0,x1=fixed_x_band(self.image_cv.shape,40,self._center_x())
        self.pending_box=((x0,min(self.v14_drag['anchor'][1],p[1])),
                          (x1,max(self.v14_drag['anchor'][1],p[1])))
        self._draw_pending(*self.pending_box)

    def _v14_release(self,event):
        if not self.v14_drag or self.v14_drag.get('which')!='draw_mirror':
            old=getattr(self,'v14_drag',None)
            out=super()._v14_release(event)
            if old and old.get('which') in ('left','right'):
                self._schedule_save()
            return out
        p=self._canvas_to_image(event.x,event.y)
        n=self.v14_drag['view']
        try:
            if p is None:raise ValueError('Release inside image')
            roi=centered_roi(self.image_cv.shape,self.v14_drag['anchor'][1],p[1],40,
                             center_x=self._center_x())
            self.views[n]['search_roi_xyxy']=roi
            self.views[n]['previous_y']=None
            self._clear_measurements()
            self.v14_hint.set(f'{VIEW_LABELS[n]} ROI saved; measure or learn seed.')
            self._schedule_save()
        except ValueError as exc:messagebox.showwarning('Mirror ROI',str(exc))
        finally:
            self.v14_drag=None;self.pending_box=None;self._roi_draw_name=None
            self.v14_mode='pan';self.tool_mode.set('pan')
            self.canvas.configure(cursor='');self._redraw()

    def _draw_pending(self,a,b):
        # Use mirror-specific palette; inherited drawing uses physical Direct orange.
        self.canvas.delete('v14_pending')
        if a is None or b is None:return
        s,ox,oy=self.display_scale,self.display_offset_x,self.display_offset_y
        x0,x1=sorted((a[0],b[0]));y0,y1=sorted((a[1],b[1]))
        n=self._roi_draw_name or 'left'
        self.canvas.create_rectangle(ox+x0*s,oy+y0*s,ox+x1*s,oy+y1*s,
              outline=PALETTE[n],width=3,dash=(5,3),tags=('v14_overlay','v14_pending'))

    def _apply_mirror_x(self):
        if self._calibration_job:
            messagebox.showwarning('Calibration busy','Do not change ROI X during calibration.');return
        if self.image_cv is None:
            messagebox.showwarning('No image','Capture image before setting X.');return
        try:x0,x1=fixed_x_band(self.image_cv.shape,40,self._center_x())
        except Exception as exc:messagebox.showerror('Invalid X',str(exc));return
        for n in ('left','right'):
            roi=self.views[n]['search_roi_xyxy']
            if roi is not None:
                self.views[n]['search_roi_xyxy']=[x0,roi[1],x1,roi[3]]
                self.views[n]['previous_y']=None
        self._clear_measurements();self._show_center_note();self._redraw();self._schedule_save()

    def _clear_measurements(self):
        self.view_results.clear();self.engines.clear();self.last_detected.clear()
        self._reset_results_table();self.f_direct_result=None
        self.delta_lr.set('ΔLR: — (not a roundness measurement)')

    def _start_seed_selection(self):
        if self._calibration_job:
            messagebox.showwarning('Calibration busy','Complete calibration before learning a new seed.');return
        return super()._start_seed_selection()

    def _finish_seed_selection(self):
        was=self._seed_mode
        super()._finish_seed_selection()
        if was and not self._seed_mode:self._schedule_save()

    def _clear_seed_model(self):
        if self._calibration_job:
            messagebox.showwarning('Calibration busy','Complete calibration before removing a seed.');return
        super()._clear_seed_model();self._schedule_save()

    def _apply_view_params(self):
        if self._calibration_job:
            messagebox.showwarning('Calibration busy','Complete calibration before changing detector parameters.');return
        self._commit_fields();self._schedule_save()
        self.v14_hint.set(f'{VIEW_LABELS[self._selected_name]} detection settings applied.')

    def _selected_changed(self,event=None):
        if self.selected_name_var.get() not in (VIEW_LABELS['left'],VIEW_LABELS['right']):
            self.selected_name_var.set(VIEW_LABELS['left'])
        return super()._selected_changed(event)

    def _load_image(self,path):
        super()._load_image(path)
        if getattr(self,'_startup_state',None) and self.image_cv_raw is not None:
            state=self._startup_state
            self._startup_state=None
            try:self._apply_setup_obj(state)
            except Exception as exc:
                self.cal_state.set('Saved setup not applied: '+str(exc))
        if hasattr(self,'cal_left'):self._update_cal_labels()

    def _camera_settings(self):
        lc=self.live_controls
        acq=self._seed_acquisition() or {}
        return {'server_url':lc.url.get(),'lens_position':acq.get('lens_position') or lc.lens.get(),
                'exposure_us':acq.get('exposure_us') or lc.exposure_us.get(),
                'analogue_gain':acq.get('analogue_gain') or lc.analogue_gain.get(),
                'focus_epoch':acq.get('focus_epoch'),
                'settings_epoch':acq.get('settings_epoch'),
                'session':acq.get('session')}

    def _begin_calibration(self,names):
        if self._calibration_job or self._seed_waiting_capture or self._seed_mode:
            messagebox.showwarning('Busy','Finish the current operation before calibrating.');return
        lc=self.live_controls
        try:
            ref=check_reference(self.cal_reference.get())
            self._commit_fields()
            self._check_mirror_prereqs(names)
            if lc.capture_inflight or lc.continuous.get():
                raise CalibrationError('Stop Continuous Measure / wait for capture first')
            if lc.background_epoch is None or self.background_cv is None:
                raise CalibrationError('Capture a NEW EMPTY background with current camera settings')
            if tuple(lc.native_size_wh)!=(4608,2592):
                raise CalibrationError('Camera must be native 4608×2592')
            lc._base_url()
        except Exception as exc:
            messagebox.showerror('Calibration setup',str(exc));return
        if not messagebox.askyesno('Working Calibration',
            f'Capture {FRAME_COUNT} NEW full-resolution GRAY8 frames per view '
            f'for {", ".join(names)}?\n'
            f'Reference Diameter = {ref:.5f} mm\n\n'
            'Keep rod, camera focus/exposure/gain, lighting and background unchanged. '
            'Nominal filament does not establish ±0.01 mm absolute accuracy.'):
            return
        self._calibration_job={'views':tuple(names),'reference':ref,
            'samples':{n:[] for n in names},'attempted':0,'reasons':[],
            'expected_epoch':tuple(lc.background_epoch),'camera':self._camera_settings(),
            'roi':{n:list(self.views[n]['search_roi_xyxy']) for n in names},
            'awaiting':False}
        self.cal_state.set('Calibration started: 10 NEW frames; previous scales are protected.')
        self._next_calibration_frame()

    def _next_calibration_frame(self):
        job=self._calibration_job
        if job is None:return
        if job['attempted']>=FRAME_COUNT:
            self._finish_calibration();return
        lc=self.live_controls
        if lc.capture_inflight:
            self._abort_calibration('Concurrent camera capture detected');return
        job['attempted']+=1
        job['awaiting']=True
        self.cal_state.set(f'Calibration acquiring frame {job["attempted"]}/{FRAME_COUNT}…')
        lc._capture('calibration')
        self._calibration_watch_id=self.after(450,self._watch_calibration)

    def _watch_calibration(self):
        self._calibration_watch_id=None
        job=self._calibration_job
        if not job or not job['awaiting']:return
        if not self.live_controls.capture_inflight:
            self._abort_calibration('Full-resolution camera capture failed; old scales retained. '
                                    +self.live_controls.info_text.get())
            return
        self._calibration_watch_id=self.after(450,self._watch_calibration)

    def _accept_calibration_snapshot(self,headers):
        job=self._calibration_job
        if job is None or not job['awaiting']:return
        lc=self.live_controls
        if lc.current_epoch!=job['expected_epoch'] or lc.background_epoch!=job['expected_epoch']:
            raise CalibrationError('Camera focus/exposure/session epoch changed; background stale')
        if self.image_cv_raw is None or tuple(self.image_cv_raw.shape[:2])!=(2592,4608):
            raise CalibrationError('Expected native 4608×2592 GRAY8 frame')
        acq=self._seed_acquisition() or {}
        if acq.get('focus_epoch')!=job['camera'].get('focus_epoch') or \
           acq.get('settings_epoch')!=job['camera'].get('settings_epoch'):
            # If these epochs are unavailable at startup, the background check above is authoritative.
            if job['camera'].get('focus_epoch') is not None:
                raise CalibrationError('Camera metadata epoch changed during calibration')
        for n in job['views']:
            if self.views[n].get('search_roi_xyxy')!=job['roi'][n]:
                raise CalibrationError(f'{n} ROI changed during calibration')
            try:
                v=dict(self.views[n],local_scale_calibration=None,previous_y=None)
                found,meas,_=measure_dynamic(self.image_cv,self.background_cv,self.image_cv_raw.shape,
                                             v,acquisition=acq)
                rec=frame_record(found,meas,acquisition=acq)
                job['samples'][n].append(rec)
            except Exception as exc:
                job['reasons'].append(f'frame {job["attempted"]} {n}: {exc}')
        job['awaiting']=False
        if self._calibration_watch_id is not None:
            try:self.after_cancel(self._calibration_watch_id)
            except tk.TclError:pass
            self._calibration_watch_id=None
        self.cal_state.set('Frame {}/{}; accepted: {}'.format(job['attempted'],FRAME_COUNT,
            ', '.join(f'{n} {len(job["samples"][n])}' for n in job['views'])))
        self.after(100,self._next_calibration_frame)

    def _finish_calibration(self):
        job=self._calibration_job
        if job is None:return
        updates={};errors=[]
        for n in job['views']:
            try:
                updates[n]=make_scale(self.views[n],job['reference'],job['samples'][n],
                           image_size_wh=list(self.live_controls.native_size_wh),
                           camera_settings=job['camera'])
            except Exception as exc:errors.append(f'{n}: {exc}')
        # For 'Both', commit atomically: neither prior scale changes if either fails.
        if errors:
            self._abort_calibration('Calibration failed; prior scales retained. '+' | '.join(errors))
            return
        for n,cal in updates.items():
            self.views[n]['local_scale_calibration']=cal
            self.views[n]['previous_y']=None
        self._calibration_job=None
        self._clear_measurements();self._update_cal_labels()
        self.cal_state.set('Working Calibration saved: '+', '.join(
           f'{n} {cal["mm_per_px"]:.8f} mm/px' for n,cal in updates.items()))
        self._save_setup(AUTOSAVE,show=False)
        messagebox.showinfo('Working Calibration complete',self.cal_state.get()+
                            '\n±0.01 mm absolute accuracy remains unverified.')

    def _abort_calibration(self,reason):
        job=self._calibration_job
        if self._calibration_watch_id is not None:
            try:self.after_cancel(self._calibration_watch_id)
            except tk.TclError:pass
        self._calibration_watch_id=None
        self._calibration_job=None
        self.cal_state.set('FAILED — '+reason)
        self._update_cal_labels()
        if job is not None:messagebox.showerror('Calibration aborted',reason)

    def _update_cal_labels(self):
        if not hasattr(self,'cal_left'):return
        for n,var in (('left',self.cal_left),('right',self.cal_right)):
            c=self.views[n].get('local_scale_calibration')
            if not c:var.set(n.title()+': Pixel Only — not calibrated');continue
            try:
                scale=validate_scale(c,view_name=n)
                qm=c.get('calibration_quality_metrics') or {}
                ts=c.get('calibration_timestamp','unknown')
                rep=qm.get('frame_std_mm')
                rep_txt=f' · Repeat σ={rep:.5f} mm' if rep is not None else ''
                cam=c.get('camera_settings') or {}
                camera_txt=' · Focus={} · Exp={}µs · Gain={}'.format(
                    cam.get('lens_position','?'),cam.get('exposure_us','?'),cam.get('analogue_gain','?'))
                qm_txt=f' · {qm.get("valid_frames","?")}/10 frames'
                var.set(f'{n.title()}: Working Calibration · Ref {float(c["rod_width_mm"]):.5f} mm · '
                        f'Scale {scale:.8f} mm/px{rep_txt}{qm_txt} · {ts}{camera_txt}')
            except Exception:var.set(n.title()+': Invalid saved calibration (Pixel Only)')

    def _setup_obj(self):
        self._commit_fields()
        size=[int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])] if self.image_cv_raw is not None else [4608,2592]
        return {'schema':'filament_dual_mirror_v1466','app_version':'v14.6.6',
                'working_rotation_degrees_clockwise':90,
                'original_image_size_wh':size,
                'shared_x_center':self.fixed_center_x_var.get(),
                'reference_diameter_mm':check_reference(self.cal_reference.get()),
                'camera_settings':self._camera_settings(),
                'saved_at':datetime.now().astimezone().isoformat(timespec='seconds'),
                'views':{n:{k:deepcopy(self.views[n].get(k)) for k in FIELDS} for n in ('left','right')}}

    def _save_setup(self,path,*,show=True):
        try:
            obj=self._setup_obj()
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            validate_saved_state(obj, obj['original_image_size_wh'])
            tmp=path.with_name(path.name+'.tmp')
            tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=_plain),encoding='utf-8')
            tmp.replace(path)
            if show:self.status_var.set('Setup saved: '+str(path))
            return True
        except Exception as exc:
            if show:messagebox.showerror('Save setup failed',str(exc))
            else:self.cal_state.set('Autosave failed: '+str(exc))
            return False

    def _save_setup_dialog(self):
        path=filedialog.asksaveasfilename(title='Save dual-mirror setup',initialfile='dual_mirror_v1466.json',
             defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:self._save_setup(path)

    def _apply_setup_obj(self,obj):
        if self.image_cv_raw is None:raise ValueError('Load a full-resolution image first')
        size=[int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])]
        new=validate_saved_state(obj,size)
        self.views.update(new)
        self.views['direct']=new_view('direct')  # never rehydrate physical Direct
        self.fixed_center_x_var.set(str(obj.get('shared_x_center',self.fixed_center_x_var.get())))
        self.cal_reference.set(f'{float(obj["reference_diameter_mm"]):.5f}')
        cam=obj.get('camera_settings') or {}
        # UI values only. Never silently reconfigure Raspberry Pi hardware.
        for attr,key in (('url','server_url'),('lens','lens_position'),('exposure_us','exposure_us'),
                         ('analogue_gain','analogue_gain')):
            if cam.get(key) is not None:getattr(self.live_controls,attr).set(str(cam[key]))
        self._selected_name='left';self.selected_name_var.set(VIEW_LABELS['left'])
        self._refresh_fields();self._update_cal_labels()
        self._clear_measurements();self._redraw()
        self.cal_state.set('Saved setup loaded. Verify Pi live camera settings and background before measurement.')

    def _load_startup_state(self):
        if not AUTOSAVE.exists():return
        try:
            obj=json.loads(AUTOSAVE.read_text(encoding='utf-8'))
            validate_saved_state(obj)
            self._startup_state=obj
            self.cal_state.set('Autosaved setup found; will restore on first full-resolution image.')
        except Exception as exc:self.cal_state.set('Autosaved setup invalid: '+str(exc))

    def _load_setup_dialog(self):
        path=filedialog.askopenfilename(title='Load v14.6.6 dual-mirror JSON',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            obj=json.loads(Path(path).read_text(encoding='utf-8'))
            validate_saved_state(obj)
            if self.image_cv_raw is None:
                self._startup_state=obj
                self.cal_state.set('Setup queued: load/capture 4608×2592 full-resolution frame to restore.')
            else:self._apply_setup_obj(obj)
        except Exception as exc:messagebox.showerror('Load setup failed',str(exc))

    def _schedule_save(self):
        if not hasattr(self,'cal_reference') or self._calibration_job:return
        if self._auto_save_id is not None:
            try:self.after_cancel(self._auto_save_id)
            except tk.TclError:pass
        self._auto_save_id=self.after(800,self._autosave_once)

    def _autosave_once(self):
        self._auto_save_id=None
        # Never overwrite a pending autosave with blank default views when the
        # user closes the GUI before the first connected full-res frame.
        if self._calibration_job or self._startup_state is not None or self.image_cv_raw is None:return
        self._save_setup(AUTOSAVE,show=False)

    def _periodic_autosave(self):
        if not self._calibration_job and self.image_cv_raw is not None:
            self._save_setup(AUTOSAVE,show=False)
        self.after(5000,self._periodic_autosave)

    def _close_app(self):
        if self._calibration_job:self._abort_calibration('GUI closing before calibration completed')
        self._autosave_once()
        try:self.live_controls.close()
        except Exception:pass
        self.destroy()


if __name__=='__main__':
    FilamentUIV1466().mainloop()

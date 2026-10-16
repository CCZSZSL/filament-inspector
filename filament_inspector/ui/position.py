#!/usr/bin/env python3
"""v14.6.7: v14.6.6 GUI + observed Y-dependent scale & one-button continuous scan.

Retains the unchanged full-resolution camera protocol, 50% intensity crossing,
seed localization and 40-column robust fusion. No ECC or physical Direct View.
"""
from __future__ import annotations
from filament_inspector.paths import DATA_ROOT
from copy import deepcopy
from datetime import datetime
import csv
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import numpy as np

from filament_inspector.ui.local_calibration import LocalCalibrationUI, AUTOSAVE, validate_saved_state
from filament_inspector.calibration.local import (
    CalibrationError, check_reference, FRAME_COUNT)
from filament_inspector.calibration.position import (
    initial_profile, validate_profile, profile_ready, with_position_point, scale_at_position)
from filament_inspector.algorithms.edges import robust_summary

ROOT=DATA_ROOT
AUTOSAVE_1467=ROOT/'config'/'dual_mirror_v1467.json'

class PositionCompensationUI(LocalCalibrationUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.6.7 | Y-Offset Compensation + Continuous')
        self._continuous_log=None
        self._continuous_count=0
        self.position_enabled=tk.BooleanVar(value=True)
        self.position_status=tk.StringVar(value='Y-Offset: untrained; original local Scale remains available.')
        self.position_runtime=tk.StringVar(value='L ΔY: — | R ΔY: — | compensation: pending')
        self.continuous_status=tk.StringVar(value='STOPPED · each sample uses fresh 4608×2592 GRAY8 PNG')

        # Keep the run/stop control visible near Camera; keep Y position
        # teaching just below Calibration so it remains in the workflow.
        base=self.adv_button.master
        for w in base.winfo_children():
            data=w.grid_info()
            if data:
                r=int(data['row'])
                if r>=6:w.grid_configure(row=r+2)
                elif r>=3:w.grid_configure(row=r+1)
        for widget in base.winfo_children():
            if isinstance(widget,ttk.Label):
                txt=str(widget.cget('text'))
                if 'Filament Inspector  v14.6.6' in txt:
                    widget.configure(text='Filament Inspector  v14.6.7')
        def relabel_center(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Label):
                    t=str(child.cget('text'))
                    if 'Dual Mirror Inspection | v14.6.6' in t:
                        child.configure(text=t.replace('v14.6.6','v14.6.7'))
                relabel_center(child)
        relabel_center(self.canvas.master)
        pos=ttk.LabelFrame(base,text='Y-Offset Compensation / Position Calibration',padding=7)
        pos.grid(row=7,column=0,sticky='ew',pady=(0,7));pos.columnconfigure(0,weight=1)
        ttk.Checkbutton(pos,text='Enable compensated mm inside learned Y range',
                        variable=self.position_enabled,command=self._toggle_position).grid(
                        row=0,column=0,sticky='w')
        ttk.Button(pos,text='Record UPPER / LOWER Point (10 frames)',
                   command=self._record_position_point).grid(row=1,column=0,sticky='ew',ipady=3,pady=(3,2))
        ttk.Button(pos,text='Clear Y-Offset Points (keep base scales)',
                   command=self._clear_position_points).grid(row=2,column=0,sticky='ew')
        ttk.Label(pos,textvariable=self.position_status,wraplength=335,justify='left').grid(
                         row=3,column=0,sticky='w',pady=(4,1))
        ttk.Label(pos,textvariable=self.position_runtime,wraplength=335,justify='left').grid(
                         row=4,column=0,sticky='w')
        ttk.Label(pos,text='Calibrate same filament at CENTER (existing scale), then physically move '
                           'it UP and DOWN along the actual operating path. Y interpolation only; '
                           'not a depth measurement.',wraplength=335).grid(row=5,column=0,sticky='w',pady=(3,0))

        cont=ttk.LabelFrame(base,text='Continuous Full-Resolution Measurement',padding=8)
        cont.grid(row=3,column=0,sticky='ew',pady=(0,7));cont.columnconfigure(0,weight=1)
        self.continuous_button=ttk.Button(cont,text='▶ START CONTINUOUS MEASUREMENT',
                                           command=self._toggle_continuous_button)
        self.continuous_button.grid(row=0,column=0,sticky='ew',ipady=6)
        rr=ttk.Frame(cont);rr.grid(row=1,column=0,sticky='w',pady=(5,2))
        ttk.Label(rr,text='Interval (s):').pack(side='left')
        ttk.Entry(rr,textvariable=self.live_controls.interval_s,width=7).pack(side='left',padx=5)
        ttk.Label(rr,text='0.5–30 seconds / frame').pack(side='left')
        ttk.Label(cont,textvariable=self.continuous_status,wraplength=335).grid(
                           row=2,column=0,sticky='w',pady=2)
        # Hide legacy duplicate checkbox/interval; preserve original scheduler.
        self._hide_legacy_continuous_checkbox()
        self.live_controls.continuous.trace_add('write',lambda *_:self._sync_continuous_controls())
        self._sync_continuous_controls()
        self._update_position_labels()
        self.v14_hint.set('v14.6.7: both mirrors use original 40-column fusion. '
                           'Optional calibrated Y-position scale corrects observed drift.')
        self._load_position_startup_state()
        self._bind_controls_wheel_recursive(base)

    def _hide_legacy_continuous_checkbox(self):
        def visit(parent):
            for child in parent.winfo_children():
                if isinstance(child,ttk.Checkbutton) and 'Continuous measure' in str(child.cget('text')):
                    child.master.pack_forget()
                    return True
                if visit(child):return True
            return False
        visit(self.live_controls)

    def _sync_continuous_controls(self):
        if not hasattr(self,'continuous_button'):return
        on=self.live_controls.continuous.get()
        self.continuous_button.configure(text=('■ STOP CONTINUOUS MEASUREMENT' if on else
                                                '▶ START CONTINUOUS MEASUREMENT'))
        if on:
            self.continuous_status.set(f'RUNNING · {self._continuous_count} captured frames · '
                                       f'interval {self.live_controls.interval_s.get()} s')
        else:
            self.continuous_status.set(f'STOPPED · {self._continuous_count} captured frames in last run')
            self._continuous_log=None

    def _toggle_continuous_button(self):
        lc=self.live_controls
        if lc.continuous.get():
            lc.continuous.set(False)
            self._toggle_mirror_continuous()  # cancels pending next interval
            self._sync_continuous_controls()
            return
        self._continuous_count=0
        lc.continuous.set(True)
        self._toggle_mirror_continuous()     # inherits all v14.6.6 prerequisites
        if lc.continuous.get():
            out=ROOT/'measurements_v1467'
            out.mkdir(parents=True,exist_ok=True)
            self._continuous_log=out/('continuous_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.csv')
            self._init_continuous_log(self._continuous_log)
        self._sync_continuous_controls()

    @staticmethod
    def _log_columns():
        return ('timestamp','frame','left_mm','right_mm','f_direct_mm','delta_lr_mm',
                'left_y_px','right_y_px','left_scale_mm_per_px','right_scale_mm_per_px',
                'left_valid_columns','right_valid_columns','left_position_mode','right_position_mode')

    @classmethod
    def _init_continuous_log(cls,path):
        with open(path,'w',newline='',encoding='utf-8') as f:
            csv.DictWriter(f,fieldnames=cls._log_columns()).writeheader()

    def _record_continuous_result(self):
        if not self.live_controls.continuous.get():return
        self._continuous_count+=1
        f=self.f_direct_result or {}
        row={'timestamp':datetime.now().astimezone().isoformat(timespec='milliseconds'),
             'frame':self._continuous_count,
             'left_mm':f.get('left_mm',''), 'right_mm':f.get('right_mm',''),
             'f_direct_mm':f.get('value',''), 'delta_lr_mm':f.get('absolute_difference_mm','')}
        for n in ('left','right'):
            m=self.view_results.get(n) or {}
            fusion=m.get('fusion') or {}
            row[n+'_y_px']=m.get('tracking_center_y_px','')
            row[n+'_scale_mm_per_px']=m.get('scale_mm_per_px','')
            row[n+'_valid_columns']=fusion.get('valid_columns','')
            row[n+'_position_mode']=(m.get('position_compensation') or {}).get('mode',
                m.get('calibration_status','FAILED'))
        if self._continuous_log:
            try:
                with open(self._continuous_log,'a',newline='',encoding='utf-8') as fp:
                    csv.DictWriter(fp,fieldnames=self._log_columns()).writerow(row)
            except OSError as exc:
                self.continuous_status.set('CSV logging error: '+str(exc))
        if self.live_controls.continuous.get():
            self.continuous_status.set(f'RUNNING · {self._continuous_count} measured frames · '
                                       f'interval {self.live_controls.interval_s.get()} s')

    def _handle_mirror_capture(self,kind,blob,headers):
        # Camera protocol and full-res decode stay in inherited code.
        super()._handle_mirror_capture(kind,blob,headers)
        if kind=='measure' and hasattr(self,'continuous_status'):
            self._record_continuous_result()

    def _record_position_point(self):
        if self._calibration_job or self._seed_waiting_capture or self._seed_mode:
            messagebox.showwarning('Busy','Finish current operation first');return
        lc=self.live_controls
        names=('left','right')
        try:
            ref=check_reference(self.cal_reference.get())
            self._commit_fields()
            self._check_mirror_prereqs(names)
            if lc.capture_inflight or lc.continuous.get():
                raise CalibrationError('Stop continuous mode and wait for outstanding capture')
            if lc.background_epoch is None or self.background_cv is None:
                raise CalibrationError('Capture an EMPTY background at unchanged camera settings')
            if tuple(lc.native_size_wh)!=(4608,2592):
                raise CalibrationError('Expected full-resolution 4608×2592 native images')
            lc._base_url()
            for n in names:
                cal=self.views[n].get('local_scale_calibration')
                if not cal:raise CalibrationError(f'{n}: calibrate base scale first')
                if abs(float(cal['rod_width_mm'])-ref)>1e-8:
                    raise CalibrationError(f'{n}: current reference diameter must equal its saved base calibration')
                self._validate_camera_and_roi(n,cal)
                prof=self.views[n].get('position_scale_profile')
                validate_profile(prof if prof else initial_profile(cal),cal)
        except Exception as exc:
            messagebox.showerror('Position calibration setup',str(exc));return
        if not messagebox.askyesno('Record position (two mirrors)',
            f'Move the SAME {ref:.5f} mm reference filament to a different vertical '
            'position on the same physical path, without moving it lengthwise.\n'
            'Keep camera/LED/background unchanged.\n\n'
            f'Capture {FRAME_COUNT} NEW images and add position anchors to BOTH mirrors? '
            'Existing base scales and previous points are protected if either view fails.'):
            return
        self._calibration_job={'views':names,'purpose':'y_position', 'reference':ref,
            'samples':{n:[] for n in names},'attempted':0,'reasons':[],
            'expected_epoch':tuple(lc.background_epoch),'camera':self._camera_settings(),
            'roi':{n:list(self.views[n]['search_roi_xyxy']) for n in names},
            'awaiting':False}
        self.cal_state.set('Recording Y position: 10 new full-res frames (both mirrors).')
        self._next_calibration_frame()

    def _validate_camera_and_roi(self,n,cal):
        roi=self.views[n].get('search_roi_xyxy') or []
        old=cal.get('search_roi_xyxy_at_calibration',[])
        if len(roi)!=4 or len(old)!=4 or (roi[0],roi[2])!=(old[0],old[2]):
            raise CalibrationError(f'{n}: fixed X band changed; base scale invalid')
        if self.image_cv_raw is not None and cal.get('original_image_size_wh') != [
            int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])]:
            raise CalibrationError(f'{n}: source image dimensions changed')
        acq=self._seed_acquisition() or {}
        prev=(cal.get('camera_settings') or {}).get('lens_position')
        now=acq.get('lens_position')
        if prev not in (None,'') and now not in (None,''):
            if abs(float(prev)-float(now))>0.01:
                raise CalibrationError(f'{n}: focus changed from base calibration; recalibrate')

    def _finish_calibration(self):
        job=self._calibration_job
        if job and job.get('purpose')=='y_position':
            updates={};errors=[]
            for n in job['views']:
                try:
                    cal=self.views[n]['local_scale_calibration']
                    profile=self.views[n].get('position_scale_profile')
                    updates[n]=with_position_point(profile,cal,job['samples'][n])
                except Exception as exc: errors.append(f'{n}: {exc}')
            if errors:
                self._abort_calibration('Position point rejected; ALL prior points/scales kept. '
                                        +' | '.join(errors))
                return
            for n,prof in updates.items():self.views[n]['position_scale_profile']=prof
            self._calibration_job=None
            self._clear_measurements();self._update_position_labels()
            self._save_setup(AUTOSAVE_1467,show=False)
            self.cal_state.set('Y-position point saved for BOTH mirrors. '
                               'Collect above/below base center before compensation becomes active.')
            messagebox.showinfo('Y-position point saved',self.position_status.get())
            return
        # Updating any base scale invalidates that view's previous Y curve.
        before={n:self._scale_fingerprint(self.views[n].get('local_scale_calibration'))
                for n in ('left','right')}
        super()._finish_calibration()
        for n in ('left','right'):
            after=self._scale_fingerprint(self.views[n].get('local_scale_calibration'))
            if after != before[n]:self.views[n].pop('position_scale_profile',None)
        if hasattr(self,'position_status'):
            self._update_position_labels()
            if not self._calibration_job:self._save_setup(AUTOSAVE_1467,show=False)

    @staticmethod
    def _scale_fingerprint(cal):
        if not cal:return None
        return (cal.get('calibration_timestamp'),cal.get('mm_per_px'),
                cal.get('rod_width_mm'),tuple(cal.get('search_roi_center_xy') or ()))

    def _update_position_labels(self):
        if not hasattr(self,'position_status'):return
        fragments=[]
        for n in ('left','right'):
            cal=self.views[n].get('local_scale_calibration')
            prof=self.views[n].get('position_scale_profile')
            if not cal:
                fragments.append(f'{n.title()}: first calibrate base scale')
            elif not prof:
                fragments.append(f'{n.title()}: base only (0 offset points)')
            else:
                try:
                    pts=validate_profile(prof,cal)
                    ready=profile_ready(prof,cal)
                    fragments.append(f'{n.title()}: {len(pts)-1} offset points · '
                        +(f'ACTIVE {pts[0][0]:.1f}…{pts[-1][0]:.1f} Y px' if ready else
                          'need points on BOTH sides of center'))
                except CalibrationError as exc:fragments.append(f'{n.title()}: invalid ({exc})')
        self.position_status.set(' | '.join(fragments))

    def _toggle_position(self):
        if self._calibration_job:
            self.position_enabled.set(not self.position_enabled.get())
            return
        self._clear_measurements()
        self._schedule_save()
        self._update_position_labels()

    def _clear_position_points(self):
        if self._calibration_job or self.live_controls.continuous.get():
            messagebox.showwarning('Busy','Stop measurement/calibration before clearing positions');return
        if not messagebox.askyesno('Clear Y offset',
            'Delete all extra Y-position points for BOTH mirrors? Original base scales remain unchanged.'):
            return
        for n in ('left','right'):self.views[n].pop('position_scale_profile',None)
        self._clear_measurements();self._update_position_labels()
        self._save_setup(AUTOSAVE_1467,show=False)

    def _process_one(self,n):
        if n=='direct':return
        cal=self.views[n].get('local_scale_calibration')
        profile=self.views[n].get('position_scale_profile')
        use=bool(hasattr(self,'position_enabled') and self.position_enabled.get() and cal and profile)
        if not use or not profile_ready_safe(profile,cal):
            super()._process_one(n)
            self._set_position_runtime(n)
            return
        # Let the UNMODIFIED detector/fuser first measure the fresh image in PX.
        # Only convert AFTER it reports the current tracked Y coordinate.
        self.views[n]['local_scale_calibration']=None
        try:super()._process_one(n)
        finally:self.views[n]['local_scale_calibration']=cal
        m=self.view_results.get(n)
        if m is None:
            self._set_position_runtime(n)
            return
        fusion=m.get('fusion') or {}
        if fusion.get('unit')!='px':
            self._seed_failure_reasons[n]='Position correction expected pixel-domain robust fusion'
            self._set_position_runtime(n)
            return
        try:
            self._validate_camera_and_roi(n,cal)
            scl=scale_at_position(profile,cal,m['tracking_center_y_px'])
        except (CalibrationError,ValueError,TypeError) as exc:
            scl=None
            reason=str(exc)
        else:reason=''
        if scl is None:
            # No silent extrapolation. Leave intact pixel result; F-Direct is
            # unavailable if either view cannot provide in-range mm.
            m['calibration_status']='Y-position OUTSIDE learned range / invalid: '+(reason or 'Pixel Only')
            m['position_compensation']={'mode':'Pixel Only: outside Y curve',
                'center_y_px':m.get('tracking_center_y_px')}
            self._put_fused_success(n,m)
            self._set_position_runtime(n)
            return
        scl=float(scl)
        for key in ('value','column_std','column_min','column_max'):
            if key in fusion and fusion[key] is not None:
                fusion[key]=float(fusion[key])*scl
        fusion['unit']='mm'
        m['fused_diameter']=fusion['value'];m['fused_unit']='mm'
        m['scale_mm_per_px']=scl
        e=self.engines[n]
        e.diameters_mm=np.where(e.valid,e.widths_px*scl,np.nan)
        e.reference_correction_factor=1.
        m['physical_stats']=robust_summary(np.asarray(e.widths_px)[e.valid]*scl)
        m['calibration_status']='MIRROR Y-INTERPOLATED SCALE / working reference'
        m['position_compensation']={'mode':'Y-interpolated','center_y_px':m['tracking_center_y_px'],
              'delta_from_base_px':float(m['tracking_center_y_px']-cal['search_roi_center_xy'][1]),
              'applied_scale_mm_per_px':scl,
              'anchor_range_y_px':[min(p['tracked_center_y_px'] for p in profile['points']),
                                  max(p['tracked_center_y_px'] for p in profile['points'])]}
        self._put_fused_success(n,m)
        self._set_position_runtime(n)

    def _set_position_runtime(self,n):
        if not hasattr(self,'position_runtime'):return
        text=[]
        for v in ('left','right'):
            m=self.view_results.get(v) or {}
            center=m.get('tracking_center_y_px')
            cal=self.views[v].get('local_scale_calibration')
            if center is None or not cal:
                text.append(f'{v[0].upper()} ΔY: —')
                continue
            dy=float(center)-float(cal['search_roi_center_xy'][1])
            status=('comp' if (m.get('position_compensation') or {}).get('mode')=='Y-interpolated'
                    else ('px-only' if (m.get('fusion') or {}).get('unit')=='px' else 'base'))
            text.append(f'{v[0].upper()} ΔY: {dy:+.1f}px ({status})')
        self.position_runtime.set(' | '.join(text))

    def _setup_obj(self):
        obj=super()._setup_obj()
        obj['app_version']='v14.6.7'
        obj['y_compensation_enabled']=bool(self.position_enabled.get()) if hasattr(self,'position_enabled') else True
        for n in ('left','right'):
            cal=self.views[n].get('local_scale_calibration')
            prof=self.views[n].get('position_scale_profile')
            if cal and prof:
                try:validate_profile(prof,cal)
                except CalibrationError:continue   # omit obsolete point set, never save unsafe stale profile
                obj['views'][n]['position_scale_profile']=deepcopy(prof)
        return obj

    def _apply_setup_obj(self,obj):
        # Validate BOTH point curves before mutating even the base GUI state.
        validate_saved_state(obj)
        restored={}
        for n in ('left','right'):
            v=obj.get('views',{}).get(n,{})
            cal=v.get('local_scale_calibration')
            prof=v.get('position_scale_profile')
            if prof is not None:
                if cal is None:raise CalibrationError(f'{n}: curve without mirror calibration')
                validate_profile(prof,cal)
                restored[n]=deepcopy(prof)
        super()._apply_setup_obj(obj)
        for n,prof in restored.items():self.views[n]['position_scale_profile']=prof
        if hasattr(self,'position_enabled'):
            self.position_enabled.set(bool(obj.get('y_compensation_enabled',True)))
            self._update_position_labels()

    def _load_position_startup_state(self):
        if AUTOSAVE_1467.exists():
            import json
            try:
                obj=json.loads(AUTOSAVE_1467.read_text(encoding='utf-8'))
                validate_saved_state(obj)
                # Later _apply_setup_obj validates the supplemental point data.
                self._startup_state=obj
                self.cal_state.set('Found v14.6.7 autosave; restore after first full-res frame.')
            except Exception as exc:self.cal_state.set('Invalid v14.6.7 autosave: '+str(exc))

    def _save_setup(self,path,*,show=True):
        # If v14.6.6 parent schedules its old autosave path, redirect it to
        # the new file; explicit user-selected paths are preserved unchanged.
        if Path(path)==AUTOSAVE:path=AUTOSAVE_1467
        return super()._save_setup(path,show=show)

    def _update_cal_labels(self):
        super()._update_cal_labels()
        if hasattr(self,'position_status'):self._update_position_labels()

    def _close_app(self):
        self.live_controls.continuous.set(False)
        super()._close_app()


def profile_ready_safe(profile,cal):
    try:return profile_ready(profile,cal)
    except (CalibrationError, KeyError, TypeError, ValueError):return False

if __name__=='__main__':
    PositionCompensationUI().mainloop()

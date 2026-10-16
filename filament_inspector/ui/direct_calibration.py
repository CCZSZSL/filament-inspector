#!/usr/bin/env python3
"""v14.6.1: 40-column fixed-X inspection with multi-frame Direct K_ref calibration.

Keeps the v14.6 UI, the original v11 50%-crossing estimator and the
v14.5 Pi server. Uses ten *fresh* PNG snapshots to estimate Direct K_ref.
"""
from __future__ import annotations

from filament_inspector.paths import DATA_ROOT
from copy import deepcopy
from pathlib import Path
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np

from filament_inspector.ui.centered import CenteredThreeViewUI
from filament_inspector.calibration.direct import aggregate_direct_frames, corrected_profile
from filament_inspector.algorithms.views import process_view, VIEW_LABELS, normalize_profile


class ThreeViewCalibratedUI(CenteredThreeViewUI):
    FRAME_COUNT=10
    MIN_GOOD=8
    DEFAULT_CENTER_X=1340  # user-validated fixed working region, 2026-10-14

    def _build_ui(self):
        self._direct_job_running=False
        self._direct_job_queue=queue.Queue()
        self._direct_original_profile=None
        super()._build_ui()
        self.title('Filament Inspector v14.6.1 | Three-view Reference Calibration')
        base=self.adv_button.master
        for w in base.grid_slaves(row=0,column=0):
            if isinstance(w,ttk.Label): w.configure(text='Filament Inspector v14.6.1')
        for w in self.canvas.master.grid_slaves(row=0,column=0):
            for child in w.winfo_children():
                if isinstance(child,ttk.Label) and 'Three-view Inspection' in child.cget('text'):
                    child.configure(text='Three-view Inspection | v14.6.1 THREE-VIEW CAL')
        self.live_controls.capture_dir=DATA_ROOT/'captures_live_v1461'
        inspect=self.v142_rod_button.master
        self.direct_cal_note=tk.StringVar(value='Direct: Homography + K_ref; 10 fresh 4608×2592 frames for K_ref.')
        # v14.2 mirror button is reused for the currently selected view.
        self.direct_reset_button=ttk.Button(inspect,text='Reset DIRECT K_ref to pre-calibration',command=self._reset_direct)
        self.direct_reset_button.grid(row=8,column=0,sticky='ew',pady=(5,0))
        ttk.Label(inspect,textvariable=self.direct_cal_note,wraplength=315,justify='left').grid(
            row=9,column=0,sticky='ew',pady=(3,0))
        self.save_direct_button=ttk.Button(inspect,text='Export DIRECT calibrated profile JSON',
                                           command=self._export_direct_profile)
        self.save_direct_button.grid(row=10,column=0,sticky='ew',pady=(4,0))
        ttk.Button(inspect,text='Save THREE-VIEW setup (ROIs + calibrations)',
                   command=self._save_layout).grid(row=11,column=0,sticky='ew',pady=(4,0))
        self._bind_controls_wheel_recursive(inspect)
        # Block the old advanced single-frame calibration route: the new Direct
        # operator action is explicit, multi-frame and auditable.
        for w in self.advanced_panel.grid_slaves(row=8,column=0):
            if isinstance(w,ttk.Button):
                w.configure(text='Calibrate DIRECT K_ref (10 fresh frames)',command=self._start_direct_calibration)
        self._refresh_fields()

    def _refresh_fields(self):
        super()._refresh_fields()
        if not hasattr(self,'direct_cal_note') or not hasattr(self,'v142_rod_button'):return
        if self._selected_name=='direct':
            self.v142_rod_button.configure(text=f'Calibrate DIRECT K_ref ({self.FRAME_COUNT} fresh frames)',
                                            state='disabled' if self._direct_job_running else 'normal',
                                            command=self._start_direct_calibration)
            profile=self.views['direct'].get('profile') or {}
            correction=profile.get('target_plane_reference_correction') or {}
            k=correction.get('correction_factor_K_ref')
            record=correction.get('record') or {}
            is_multi=record.get('schema')=='direct_reference_multiframe_v1461'
            self.direct_cal_note.set(f'Direct K_ref: {float(k):.8f} | '+
                        ('10-frame rod calibration · NOT independent validation' if is_multi else 'existing profile (verify focus/ROI)')
                        if k is not None and correction.get('enabled') else
                        'Direct: planar H loaded but K_ref uncalibrated. Capture 10 fresh frames.')
            self.direct_reset_button.configure(state='normal' if self._direct_original_profile is not None else 'disabled')
            self.save_direct_button.configure(state='normal' if profile else 'disabled')
        else:
            self.v142_rod_button.configure(text='Calibrate MIRROR scale (2.000 mm rod)',
                                            state='normal',command=self._calibrate_mirror_scale)
            self.direct_reset_button.configure(state='disabled')
            self.save_direct_button.configure(state='disabled')
            self.direct_cal_note.set('Mirror: local mm/px calibration from reference rod; independently verify with 1.75 mm filament.')

    def _start_direct_calibration(self):
        if self._direct_job_running:return
        if self._selected_name!='direct':
            messagebox.showwarning('Select Direct View','Select Direct View to calibrate its K_ref.');return
        self._commit_fields()
        lc=self.live_controls
        if self.image_cv_raw is None or self.background_cv is None or lc.background_epoch is None:
            messagebox.showwarning('Missing reference', 'Connect live camera, capture EMPTY background, insert 2 mm rod and draw ROIs first.');return
        if lc.capture_inflight or lc.continuous.get():
            messagebox.showwarning('Acquisition busy', 'Stop continuous measurement and wait for current capture to finish.');return
        v=self.views['direct']
        if v['profile'] is None:
            messagebox.showwarning('No Homography','Load the Direct View planar Homography profile before calibrating K_ref.');return
        roi=v.get('search_roi_xyxy')
        if roi is None or roi[2]-roi[0]+1!=self.FIXED_COLUMNS:
            messagebox.showwarning('ROI','Set the fixed 40-pixel Direct ROI first.');return
        try:
            known=float(self.known_width_var.get())
            center=self._center_x()
            if not np.isfinite(known) or not 0<known<20:raise ValueError('Reference diameter must be between 0 and 20 mm')
            focus=float(lc.lens.get());exposure=int(lc.exposure_us.get());gain=float(lc.analogue_gain.get())
            if not np.isfinite(focus) or not np.isfinite(gain):raise ValueError('Invalid camera parameter')
            expected_epoch=lc.background_epoch
            expected_shape=tuple(lc.native_size_wh)
            if expected_shape!=(4608,2592):raise ValueError('Expected 4608×2592 camera resolution')
            normalized=normalize_profile(v['profile'],'direct')
        except (ValueError,TypeError,tk.TclError) as exc:
            messagebox.showerror('Calibration setup error',str(exc));return
        if not messagebox.askyesno('Confirm 2 mm working reference',
               f'Calibrate Direct K_ref using {self.FRAME_COUNT} NEW 4608×2592 snapshots of the {known:.3f} mm rod?\n\n'
               f'Fixed ROI X={center:g}, 40 px. LensPosition={focus:.3f}.\n'
               'Keep rod, focus, lighting and exposure unchanged.\n'
               'Existing Homography will NOT be modified.\n\n'
               'This is calibration, NOT independent accuracy validation.'):
            return
        base_url=lc._base_url()
        self._direct_job_running=True
        self._direct_expected={'epoch':expected_epoch,'roi':list(roi),'focus':focus,'exposure':exposure,'gain':gain,'center':center,
                               'size':expected_shape,'known':known,'profile':deepcopy(normalized)}
        self.direct_cal_note.set(f'Direct calibration: acquiring {self.FRAME_COUNT} new full-resolution frames...')
        self.v142_rod_button.configure(state='disabled')
        bg=self.background_cv.copy()
        settings=deepcopy(v)
        settings['profile']=deepcopy(normalized)
        settings['previous_y']=None
        t=threading.Thread(target=self._direct_worker,args=(base_url,bg,settings,self._direct_expected.copy()),daemon=True)
        t.start()
        self.after(100,self._poll_direct_job)

    def _direct_worker(self,base_url,background,view,initial):
        frames=[];reasons=[]
        for i in range(self.FRAME_COUNT):
            try:
                blob,headers=self.live_controls._request(base_url,'/snapshot.png',timeout=60)
                epoch=(headers.get('X-Session-ID',headers.get('X-Session-Id','')),
                       headers.get('X-Focus-Epoch',''),headers.get('X-Settings-Epoch','0'))
                if epoch != initial['epoch']:
                    raise RuntimeError('Camera session, focus or exposure epoch changed; aborted (no profile modified).')
                if abs(float(headers.get('X-Lens-Position','nan'))-initial['focus'])>0.002:
                    raise RuntimeError('LensPosition changed; aborted (no profile modified).')
                if (int(headers.get('X-Exposure-Time-Us','-1')) != initial['exposure'] or
                    abs(float(headers.get('X-Analogue-Gain','nan'))-initial['gain'])>0.001):
                    raise RuntimeError('Exposure/gain changed; aborted (no profile modified).')
                raw=cv2.imdecode(np.frombuffer(blob,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
                if raw is None or raw.ndim!=2 or raw.dtype!=np.uint8 or raw.shape!=(initial['size'][1],initial['size'][0]):
                    raise RuntimeError('Snapshot is not full-resolution 8-bit grayscale PNG')
                working=cv2.cvtColor(cv2.rotate(raw,cv2.ROTATE_90_CLOCKWISE),cv2.COLOR_GRAY2BGR)
                found,meas,_=process_view(working,background,raw.shape,view,operation='calibrate',known_mm=initial['known'])
                valid,total=meas['valid_slices'],meas['total_slices']
                if valid/max(1,total)<0.85:raise ValueError(f'Only {valid}/{total} valid slices')
                stats=meas['raw_homography_stats']
                if not stats or not np.isfinite(float(stats['median'])):raise ValueError('Invalid raw H measurements')
                frames.append({'sample':i+1,'raw_median_mm':float(stats['median']),
                               'valid':int(valid),'total':int(total),'roi':list(meas['roi'])})
            except RuntimeError as exc:
                self._direct_job_queue.put(('done',False,str(exc)));return
            except Exception as exc:
                reasons.append(f'Frame {i+1}: {exc}')
            self._direct_job_queue.put(('progress',i+1,len(frames)))
            time.sleep(0.12)
        try:
            agg=aggregate_direct_frames(frames,initial['known'],requested_count=self.FRAME_COUNT,minimum_good=self.MIN_GOOD)
            self._direct_job_queue.put(('done',True,(agg,reasons)))
        except Exception as exc:
            self._direct_job_queue.put(('done',False,str(exc)+'\n'+'\n'.join(reasons[:3])))

    def _poll_direct_job(self):
        if not self.winfo_exists():return
        finished=False
        for _ in range(16):
            try:event=self._direct_job_queue.get_nowait()
            except queue.Empty:break
            if event[0]=='progress':
                self.direct_cal_note.set(f'NEW frames: {event[1]}/{self.FRAME_COUNT} | accepted {event[2]}')
            elif event[0]=='done':
                finished=True
                self._direct_job_running=False
                if event[1]:
                    try:self._commit_direct_calibration(*event[2])
                    except Exception as exc:messagebox.showerror('Direct calibration rejected',str(exc))
                else:messagebox.showerror('Direct calibration rejected',event[2])
                self._refresh_fields()
        if not finished:self.after(120,self._poll_direct_job)

    def _commit_direct_calibration(self,agg,reasons):
        start=self._direct_expected
        lc=self.live_controls
        if lc.background_epoch != start['epoch'] or self.views['direct']['search_roi_xyxy'] != start['roi']:
            raise RuntimeError('Background/camera epoch or ROI changed during calibration. No correction applied.')
        if abs(self._center_x()-start['center'])>0.01:
            raise RuntimeError('Locked ROI X changed during calibration. No correction applied.')
        original=self.views['direct']['profile']
        if original is None:raise ValueError('Direct Homography was removed during calibration')
        if original['local_planar_calibration'] != start['profile']['local_planar_calibration']:
            raise ValueError('Direct Homography changed during calibration')
        updated=corrected_profile(original,agg,known_mm=start['known'],sensor_size_wh=start['size'],
             roi=start['roi'],lens_position=start['focus'],exposure_us=start['exposure'],
             analogue_gain=start['gain'],x_center=start['center'])
        self._direct_original_profile=deepcopy(original)  # exact in-session rollback
        self.views['direct']['profile']=updated
        self.views['direct']['previous_y']=None
        self.view_results.clear();self.engines.clear();self.last_detected.clear()
        self._reset_results_table()
        self._refresh_fields()
        self._process_one('direct')
        self._redraw()
        messagebox.showinfo('DIRECT calibration stored',
          f'Reference: {start["known"]:.3f} mm\nFresh frames: {agg["accepted_count"]}/{agg["requested_count"]}\n'
          f'Raw Homography median: {agg["raw_homography_median_mm"]:.6f} mm\n'
          f'New K_ref: {agg["K_ref"]:.8f}\n'
          f'Across-frame median STD: {agg["frame_median_sample_std_mm"]*1000:.2f} µm\n\n'
          f'Click Save THREE-VIEW setup to persist. Verify later with a DIFFERENT diameter.')

    def _reset_direct(self):
        if self._direct_job_running or self._direct_original_profile is None:return
        if not messagebox.askyesno('Reset Direct calibration','Restore the exact previous Direct profile from before this calibration?'):return
        self.views['direct']['profile']=deepcopy(self._direct_original_profile)
        self._direct_original_profile=None
        self.view_results.clear();self.engines.clear();self.last_detected.clear()
        self._reset_results_table();self._refresh_fields();self._redraw()

    def _export_direct_profile(self):
        profile=self.views['direct'].get('profile')
        if profile is None:messagebox.showwarning('No profile','Load the Direct calibration profile first.');return
        p=filedialog.asksaveasfilename(title='Export Direct planar H + K_ref profile',
            initialfile='direct_view_calibration_v1461.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if not p:return
        Path(p).write_text(json.dumps(profile,indent=2),encoding='utf-8')
        self.views['direct']['profile_path']=str(Path(p).resolve())
        self._refresh_fields()

    def _process_one(self,n):
        # Never silently apply this local rod correction at a different optical
        # setting or ROI. Older profiles lacking v14.6.1 metadata are unchanged.
        if n=='direct':
            profile=self.views['direct'].get('profile') or {}
            correction=profile.get('target_plane_reference_correction') or {}
            record=(correction.get('record') or {})
            if correction.get('enabled') and record.get('schema')=='direct_reference_multiframe_v1461':
                try:
                    focus=float(self.live_controls.lens.get())
                    expected=float(record['lens_position_commanded'])
                    roi=self.views['direct']['search_roi_xyxy']
                    cal_roi=record['search_roi_xyxy_at_calibration']
                    if abs(focus-expected)>0.005 or roi is None or (roi[0],roi[2]) != (cal_roi[0],cal_roi[2]):
                        self.last_detected.pop(n,None)
                        self._put_failure(n,'CALIBRATION MISMATCH',
                           'Direct K_ref was calibrated at a different LensPosition or X ROI. '
                           'Restore its calibration settings, or recalibrate with the reference rod.')
                        return
                except (ValueError,TypeError,KeyError) as exc:
                    self._put_failure(n,'INVALID CALIBRATION',str(exc));return
        return super()._process_one(n)

    def _calibrate_rod(self):
        # Replace the inherited single-frame legacy Direct calibration route.
        if self._selected_name=='direct':return self._start_direct_calibration()
        return self._calibrate_mirror_scale()

    def _load_layout(self):
        if self._direct_job_running:
            messagebox.showwarning('Calibrating','Cannot load ROI setup during Direct calibration.');return
        super()._load_layout()
        self._direct_original_profile=None
        self._refresh_fields()


if __name__ == '__main__':
    ThreeViewCalibratedUI().mainloop()

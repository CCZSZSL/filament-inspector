#!/usr/bin/env python3
"""v14.6.3-F: base v14.6.3, two mirrors + virtual F-Direct View.

No v14.6.4 ECC registration or background edge-map is imported or used.
The real Direct View remains as a geometry guide ONLY, never measured.
"""
from __future__ import annotations
from filament_inspector.paths import DATA_ROOT
import csv
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
from filament_inspector.ui.dynamic import DynamicFusionThreeViewUI
from filament_inspector.algorithms.dynamic import measure_dynamic
from filament_inspector.algorithms.views import VIEW_LABELS
from filament_inspector.algorithms.fusion import fuse_mirror_views, MirrorFusionError


class FDirectTwoMirrorUI(DynamicFusionThreeViewUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.6.3-F | DUAL MIRROR + F-DIRECT VIEW')
        self.live_controls.capture_dir=DATA_ROOT/'captures_live_v1463_f_direct'
        self.known_width_var.set(1.75)  # nominal working reference; user can edit this
        for parent in (self.adv_button.master,self.canvas.master):
            for widget in parent.winfo_children():
                stack=[widget]
                while stack:
                    child=stack.pop()
                    if isinstance(child,ttk.Label):
                        t=str(child.cget('text'))
                        if 'Three-view Inspection | v14.6.3' in t:
                            child.configure(text='Dual-mirror Inspection | v14.6.3-F | F-Direct View')
                        elif t=='Filament Inspector v14.6.3':
                            child.configure(text='Filament Inspector v14.6.3-F')
                        elif 'Three-view results' in t:
                            child.configure(text='Dual-mirror results — F-Direct View is virtual')
                    elif isinstance(child,ttk.Button):
                        t=str(child.cget('text'))
                        if 'MEASURE ALL THREE VIEWS' in t or 'PROCESS ALL THREE VIEWS' in t or 'Capture + MEASURE 3 views' in t:
                            child.configure(text='MEASURE 2 MIRRORS + F-DIRECT')
                    stack.extend(child.winfo_children()
                                 )
        # Direct row is reserved for the virtual measurement, not raw Direct.
        self.f_direct_result=None
        self._show_f_direct_waiting('WAITING FOR MIRRORS')
        self.v14_hint.set('Physical Direct view DISABLED. Measure Left and Right mirrors; '
                          'F-Direct = their calibrated mm average. Mirror pixel values are never averaged.')
        self._refresh_fields()

    def _refresh_fields(self):
        super()._refresh_fields()
        if not hasattr(self,'v142_rod_button') or not hasattr(self,'direct_cal_note'):
            return
        if self._selected_name=='direct':
            self.v142_rod_button.configure(text='F-Direct is virtual (no K_ref calibration)',state='disabled')
            self.direct_cal_note.set('F-Direct = (Left Mirror mm + Right Mirror mm) / 2. '
                                     'This physical center view is a ROI layout GUIDE ONLY. '
                                     'No Direct H × K_ref measurement.')
            if hasattr(self,'direct_reset_button'):self.direct_reset_button.configure(state='disabled')
            if hasattr(self,'save_direct_button'):self.save_direct_button.configure(state='disabled')
            if hasattr(self,'v14_cal_label'):self.v14_cal_label.set('F-Direct View: virtual mirror mean; calibrate both mirrors independently')
        else:
            self.v142_rod_button.configure(text='Calibrate MIRROR (current reference, working)',
                                            state='normal',command=self._calibrate_mirror_scale)
            self.direct_cal_note.set('Select a mirror seed, set reference width in Advanced settings '
                                     '(1.750 mm nominal filament is PROVISIONAL), then calibrate both mirrors.')

    def _refresh_seed_note(self):
        super()._refresh_seed_note()
        if getattr(self,'_selected_name',None)=='direct' and hasattr(self,'seed_note'):
            self.seed_note.set('F-Direct View is synthesized from the two mirrors. '
                               'Do NOT select a center-view sample. Select left/right separately.')

    def _start_seed_selection(self):
        if self._selected_name=='direct':
            messagebox.showinfo('Virtual F-Direct','Select Left Mirror or Right Mirror to capture the ONE interior seed.')
            return
        return super()._start_seed_selection()

    def _reset_results_table(self):
        super()._reset_results_table()
        if hasattr(self,'result_table'):
            self._show_f_direct_waiting('WAITING FOR MIRRORS')
        if hasattr(self,'f_direct_result'):self.f_direct_result=None

    def _show_f_direct_waiting(self,state):
        if hasattr(self,'result_table'):
            self.result_table.item('direct',values=['F-Direct View',state,'VIRTUAL','—','—','—','—','—'])

    def _process_one(self,n):
        if n=='direct':
            self._update_f_direct()
            return
        return super()._process_one(n)

    def _update_f_direct(self):
        self.view_results.pop('direct',None)
        self.engines.pop('direct',None)
        self.last_detected.pop('direct',None)
        try:
            f=fuse_mirror_views(self.view_results)
            self.f_direct_result=f
            self.result_table.item('direct',values=[
                'F-Direct View','FUSED mm','L + R (virtual)',
                f'{f["valid_columns_left"]}/{f["valid_columns_right"]}',
                f'{f["value"]:.5f}mm',f'ΔLR={f["absolute_difference_mm"]:.5f}mm',
                '—', '—'])
            self.detail_var.set('F-Direct View | FUSED Ø={:.5f} mm | Left={:.5f} mm | '
                                'Right={:.5f} mm | ΔLR={:.5f} mm | virtual mean, NOT actual Direct '
                                '| ±0.01 mm accuracy NOT validated'.format(
                                    f['value'],f['left_mm'],f['right_mm'],f['absolute_difference_mm']))
        except MirrorFusionError as exc:
            self.f_direct_result=None
            self._show_f_direct_waiting('NOT AVAILABLE')
            self.detail_var.set('F-Direct View unavailable: '+str(exc))
        return self.f_direct_result

    def process_selected(self):
        # Even "Measure one" uses a single current frame for BOTH mirrors.
        # Never mix current Left with an older Right measurement.
        self.process_all()

    def process_all(self):
        self._commit_fields()
        self.f_direct_result=None
        self.view_results.clear();self.engines.clear();self.last_detected.clear()
        self._reset_results_table()
        for n in ('left','right'):
            self._process_one(n)
        f=self._update_f_direct()
        self.status_var.set('Left: {} | Right: {} | F-Direct: {}'.format(
            self.result_table.item('left','values')[1],
            self.result_table.item('right','values')[1],
            'FUSED mm' if f else 'NOT AVAILABLE'))
        self.view_mode.set('original')
        self._redraw()

    def _v14_process_all(self):
        try:
            self._require_photos()
            if any(self.views[n].get('search_roi_xyxy') is None for n in ('left','right')):
                raise ValueError('Set Left and Right mirror ROIs first. Direct ROI is optional for measurement.')
            # Shared X is validated only between the two ACTIVE mirror views.
            x0=self.views['left']['search_roi_xyxy']
            x1=self.views['right']['search_roi_xyxy']
            if (x0[0],x0[2])!=(x1[0],x1[2]) or x0[2]-x0[0]+1!=40:
                raise ValueError('Two mirrors must use a shared fixed 40-column X band')
        except Exception as exc:
            messagebox.showwarning('Setup incomplete',str(exc));return
        self.process_all()
        if self.f_direct_result:
            f=self.f_direct_result
            self.v14_hint.set(f'F-Direct View = {f["value"]:.5f} mm '
                               f'(L {f["left_mm"]:.5f}, R {f["right_mm"]:.5f}, '
                               f'ΔLR {f["absolute_difference_mm"]:.5f} mm). Virtual, not a third view.')
        else:
            reason=' | '.join(self._seed_failure_reasons.get(n,'') for n in ('left','right') if self._seed_failure_reasons.get(n))
            self.v14_hint.set('Mirrors measured independently. F-Direct requires BOTH valid calibrated-mm results. '+reason[:260])

    def _v14_process_one(self):
        self._v14_process_all()

    def _selected_diagnostic(self):
        if self._selected_name=='direct':
            messagebox.showinfo('F-Direct has no original edges',
                'F-Direct View is a mathematical average only. '
                'Select Left Mirror or Right Mirror for 40-column edge diagnostics.')
            return
        return super()._selected_diagnostic()

    def _show_seed_diagnostic(self):
        if self._selected_name=='direct':
            messagebox.showinfo('F-Direct has no seed',
                'Select Left Mirror or Right Mirror for seed/background diagnostics.')
            return
        return super()._show_seed_diagnostic()

    def _calibrate_mirror_scale(self):
        """Working calibration using the SAME v14.6.3 dynamic fused pixels.

        Explicitly provisional if known width is a nominal 1.75 mm filament.
        The reference itself is not an independent accuracy validation.
        """
        n=self._selected_name
        if n not in ('left','right'):
            messagebox.showinfo('Mirror only','Select Left Mirror or Right Mirror first.');return
        try:
            self._commit_fields()
            self._require_photos()
            known=float(self.known_width_var.get())
            if not np.isfinite(known) or not 0<known<20:
                raise ValueError('Reference width must be >0 and <20 mm')
            view=self.views[n]
            if view.get('seed_gray_model') is None:
                raise ValueError('Select ONE interior seed on this mirror first')
            fresh_view=dict(view,local_scale_calibration=None,previous_y=None)
            found,m,e=measure_dynamic(self.image_cv,self.background_cv,
                self.image_cv_raw.shape,fresh_view,acquisition=self._seed_acquisition())
            f=m['fusion']
            if f['unit']!='px' or int(f['valid_columns'])<35:
                raise ValueError('Need at least 35 good fused PIXEL columns for working calibration')
            if float(f['edge_fit_residual_median_px'])>0.8:
                raise ValueError('Edge fit residual too high for working calibration')
            measured_px=float(f['value'])
            roi=list(view['search_roi_xyxy'])
        except Exception as exc:
            messagebox.showerror('Cannot calibrate mirror',str(exc));return
        if not messagebox.askyesno('PROVISIONAL mirror working calibration',
                f'{VIEW_LABELS[n]}\nReference diameter = {known:.3f} mm\n'
                f'40-column fused width = {measured_px:.4f} px\n'
                f'Set local scale = {known/measured_px:.8f} mm/px?\n\n'
                'Ensure BOTH outer edges are correct in the image. '
                'If the reference is nominal filament, absolute accuracy ±0.01 mm '
                'is NOT established. No independent validation is performed.'):
            return
        cal={'schema':'mirror_local_scale_v142','view_name':VIEW_LABELS[n],
             'working_rotation_degrees_clockwise':90,
             'original_image_size_wh':[int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])],
             'rod_width_mm':known,'rod_median_width_px':measured_px,
             'rod_pixel_std':float(f['column_std']),
             'rod_valid_slices':int(f['valid_columns']),'rod_total_slices':40,
             'mm_per_px':known/measured_px,
             'search_roi_xyxy_at_calibration':roi,
             # Use ACTUAL tracked center (not search band centre).
             'search_roi_center_xy':[(roi[0]+roi[2])/2,float(found['center_y'])],
             'method':'v1463_dynamic_seed_40column_fused_projection',
             'reference_image':str(self.image_path),
             'reference_type':'provisional_working_reference',
             'accuracy_validated':False,
             'note':'Reference nominal filament may not equal true 1.750mm; not accuracy validation.'}
        self.views[n]['local_scale_calibration']=cal
        self.views[n]['previous_y']=None
        self._refresh_fields()
        self.view_results.clear();self.engines.clear();self.last_detected.clear()
        self._reset_results_table()
        self.v14_hint.set(f'{VIEW_LABELS[n]} local scale saved in memory ({known:.3f} mm working reference). '
                           'Calibrate other mirror, then SAVE THREE-VIEW setup.')
        messagebox.showinfo('Working scale stored',
                f'{VIEW_LABELS[n]}: {cal["mm_per_px"]:.8f} mm/px (PROVISIONAL).\n'
                'Calibrate the second mirror and save THREE-VIEW setup JSON.')

    def _export_results(self):
        if not self.view_results:
            messagebox.showwarning('No results','Capture and measure both mirrors first.');return
        path=filedialog.asksaveasfilename(initialfile='two_mirror_f_direct_v1463.json',
            defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        obj={'schema':'filament_dual_mirror_f_direct_results_v1463',
             'original_frame':str(self.image_path) if self.image_path else None,
             'background':str(self.background_path) if self.background_path else None,
             'working_rotation_degrees_clockwise':90,
             'formula':'F-Direct View = (Left_mm + Right_mm)/2; no real Direct measurement',
             'f_direct':self.f_direct_result,
             'roundness':'NOT measured',
             'accuracy_statement':'±0.01mm target only, NOT independently validated',
             'views':{n:self.view_results.get(n) for n in ('left','right')}}
        def serialize(v):
            if isinstance(v,np.ndarray):return v.tolist()
            if isinstance(v,np.generic):return v.item()
            raise TypeError(type(v).__name__)
        Path(path).write_text(json.dumps(obj,indent=2,default=serialize),encoding='utf-8')
        self.status_var.set('Saved F-Direct results: '+Path(path).name)

    def _export_csv(self):
        if not self.engines:
            messagebox.showwarning('No results','Measure both mirrors first.');return
        path=filedialog.asksaveasfilename(initialfile='two_mirror_f_direct_columns_v1463.csv',
            defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        with open(path,'w',newline='',encoding='utf-8') as handle:
            writer=csv.writer(handle)
            writer.writerow(['record_type','view','column_index','valid','accepted_in_fusion',
                             'top_y_roi_px','bottom_y_roi_px','width_px',
                             'width_mm_if_calibrated','fusion_mm','calibration_status'])
            for n in ('left','right'):
                e=self.engines.get(n)
                m=self.view_results.get(n)
                if e is None or m is None:continue
                f=m.get('fusion') or {}
                accepted=set(f.get('accepted_indices',[]))
                for i,ok in enumerate(e.valid):
                    mm=float(e.diameters_mm[i]) if ok and e.diameters_mm is not None else ''
                    writer.writerow(['measured_column',VIEW_LABELS[n],i,bool(ok),i in accepted,
                        float(e.top_edges[i]) if ok else '',
                        float(e.bottom_edges[i]) if ok else '',
                        float(e.widths_px[i]) if ok else '',mm,
                        f['value'] if f.get('unit')=='mm' else '',m.get('calibration_status','')])
            if self.f_direct_result:
                writer.writerow(['virtual_fusion','F-Direct View','','','','','','','',
                                 self.f_direct_result['value'],
                                 self.f_direct_result['calibration_status']])
        self.status_var.set('Saved mirror columns + virtual F-Direct: '+Path(path).name)


if __name__=='__main__':
    FDirectTwoMirrorUI().mainloop()

#!/usr/bin/env python3
"""v14.6.3 — One-time grayscale learning + moving-filament tracking + 40-col fusion.

Built on v14.6.2.2 GUI; Raspberry Pi camera server and saved v14 setups unchanged.
"""
from __future__ import annotations
import tkinter as tk
from pathlib import Path
import json
import csv
import numpy as np
from tkinter import ttk, messagebox, filedialog
from filament_inspector.ui.seed import SeedGuidedThreeViewUI
from filament_inspector.ui.three_view import ThreeViewUI
from filament_inspector.algorithms.views import VIEW_LABELS
from filament_inspector.algorithms.seed import SeedDetectionError
from filament_inspector.algorithms.dynamic import measure_dynamic


class DynamicFusionThreeViewUI(SeedGuidedThreeViewUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Inspector v14.6.3 | DYNAMIC TRACKING + 40-COLUMN FUSION')
        for parent in (self.adv_button.master,self.canvas.master):
            for widget in parent.winfo_children():
                stack=[widget]
                while stack:
                    child=stack.pop()
                    if isinstance(child,ttk.Label):
                        t=str(child.cget('text'))
                        if 'Three-view Inspection | v14.6.2.2' in t:
                            child.configure(text='Three-view Inspection | v14.6.3 DYNAMIC FUSION')
                        elif 'Filament Inspector v14.6.2.2' in t:
                            child.configure(text='Filament Inspector v14.6.3')
                    stack.extend(child.winfo_children())
        self.result_table.heading('median',text='FUSED Ø')
        self.result_table.heading('mean',text='Col. Std')
        self.result_table.heading('std',text='Fit residual')
        self.result_table.heading('confidence',text='Quality*')
        self.seed_note.set('Learn grayscale ONCE with a small interior sample; subsequent captures track the filament anywhere inside this view search Y range.')

    def _refresh_seed_note(self):
        super()._refresh_seed_note()
        if not hasattr(self,'seed_note') or not hasattr(self,'views'):return
        n=self._selected_name
        if self._seed_mode:return
        model=self.views[n].get('seed_gray_model')
        if model:
            anchor=model.get('tracking_reference_center_y')
            self.seed_note.set(
                f'{VIEW_LABELS[n]}: ONE-TIME GRAY8 MODEL ACTIVE | '
                f'{len(model["samples"])} seed(s) | median={model["median"]:.1f}, MAD={model["mad"]:.1f}. '
                'No fixed sample-Y requirement; track within current search band.'+
                (f' Tracking reference Y={anchor:.1f}.' if anchor is not None else ''))

    def _process_one(self,n):
        view=self.views[n]
        if not view.get('seed_gray_model'):
            # Old layouts and models without seed remain backward compatible;
            # legacy result is not spuriously labeled FUSED.
            return super()._process_one(n)
        self._seed_failure_diagnostics.pop(n,None)
        self._seed_failure_reasons.pop(n,None)
        try:
            self._require_photos()
            if view.get('search_roi_xyxy') is None:raise ValueError('Search ROI missing')
            if n=='direct':
                profile=view.get('profile') or {}
                correction=profile.get('target_plane_reference_correction') or {}
                rec=correction.get('record') or {}
                if correction.get('enabled') and rec.get('schema')=='direct_reference_multiframe_v1461':
                    focus=float(self.live_controls.lens.get())
                    refroi=rec['search_roi_xyxy_at_calibration']
                    search=view['search_roi_xyxy']
                    if (abs(focus-float(rec['lens_position_commanded']))>0.005 or
                        (search[0],search[2])!=(refroi[0],refroi[2])):
                        raise ValueError('Direct K_ref focus / fixed-X mismatch; restore reference calibration conditions')
            found,m,e=measure_dynamic(self.image_cv,self.background_cv,self.image_cv_raw.shape,view,
                                      acquisition=self._seed_acquisition())
            view['previous_y']=found['center_y']
            # Do not anchor tracking to where the user selected a patch: use the
            # centre of the FIRST successfully detected silhouette as reference.
            model=view['seed_gray_model']
            if model.get('tracking_reference_center_y') is None:
                model['tracking_reference_center_y']=found['center_y']
                m['tracking_reference_delta_y_px']=0.
            self.last_detected[n]=found
            self.view_results[n]=m
            self.engines[n]=e
            self._put_fused_success(n,m)
        except Exception as exc:
            self.last_detected.pop(n,None)
            if isinstance(exc,SeedDetectionError) and exc.diagnostic:
                self._seed_failure_diagnostics[n]=exc.diagnostic
            self._seed_failure_reasons[n]=str(exc)
            if n not in self._seed_failure_diagnostics:
                self._seed_failure_diagnostics[n]=self._fallback_seed_diagnostic(n)
            diag=self._seed_failure_diagnostics.get(n)
            if diag is not None:diag['failure_reason']=str(exc)
            self._put_failure(n,'TRACK/FUSE FAILED',str(exc))

    def _put_fused_success(self,n,m):
        f=m['fusion']
        unit=f['unit']
        status='FUSED mm' if unit=='mm' else 'FUSED PIXEL ONLY'
        if m.get('tracking_reference_delta_y_px') is not None:
            shift=f'{m["tracking_reference_delta_y_px"]:+.1f}px'
        else:shift='uninitialized'
        self.result_table.item(n,values=[VIEW_LABELS[n],status,
            str(m['roi']),f'{f["valid_columns"]}/{f["total_columns"]}',
            f'{f["value"]:.5f}{unit}' if unit=='mm' else f'{f["value"]:.3f}px',
            f'{f["column_std"]:.5f}{unit}' if unit=='mm' else f'{f["column_std"]:.3f}px',
            f'{f["edge_fit_residual_median_px"]:.2f}px',
            f'{m["confidence_heuristic"]:.2f}'])
        self.detail_var.set(
            f'{VIEW_LABELS[n]} | FUSED Ø={f["value"]:.5f} {unit} | '
            f'{f["valid_columns"]}/40 columns | 40-col scatter={f["column_std"]:.5f} {unit} | '
            f'fit residual={f["edge_fit_residual_median_px"]:.2f}px | '
            f'Y shift from first detection={shift} | {m["calibration_status"]}. '
            'Fused accuracy ±0.01 mm is a TARGET, NOT YET VERIFIED.')

    def _selected_diagnostic(self):
        # Parent 14.6.2 diagnostic includes original gray8 edge-crossing graph,
        # but recognizes only the old method tag. Reuse it without changing
        # the real stored method or measured data.
        result=self.view_results.get(self._selected_name)
        if result is None or result.get('detection_method')!='dynamic_seed_guided':
            return super()._selected_diagnostic()
        result['detection_method']='seed_guided'
        try:return super()._selected_diagnostic()
        finally:result['detection_method']='dynamic_seed_guided'

    def _export_results(self):
        if not self.view_results:
            messagebox.showwarning('No results','Measure at least one view first.');return
        path=filedialog.asksaveasfilename(initialfile='three_view_fused_results_v1463.json',
            defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        obj={'schema':'filament_three_view_fused_results_v1463',
             'original_frame':str(self.image_path) if self.image_path else None,
             'background':str(self.background_path) if self.background_path else None,
             'working_rotation_degrees_clockwise':90,
             'fusion_definition':'40 original-GRAY8 columns of an axial segment; paired robust edge fits, normal width, 10% trimmed mean',
             'accuracy_statement':'Target ±0.01mm, NOT yet independently validated',
             'quality_statement':'Confidence heuristic is NOT an accuracy probability',
             'roundness':'NOT measured','views':{}}
        for n,m in self.view_results.items():
            found=self.last_detected.get(n,{})
            allowed=('measurement_roi_xyxy','search_xyxy','foreground_rough_edge_y',
                     'center_y','coarse_width_px','adaptive_threshold_gray',
                     'foreground_contrast','foreground_column_coverage','candidates',
                     'valid_guide_columns','tracking_delta_y_px','seed_model_summary')
            obj['views'][n]={'localization':{k:found[k] for k in allowed if k in found},
                'measurement':m,'calibration_status':m.get('calibration_status'),
                'seed_sample_count':len((self.views[n].get('seed_gray_model') or {}).get('samples',[]))}
        def serialize(v):
            if isinstance(v,np.ndarray):return v.tolist()
            if isinstance(v,np.generic):return v.item()
            raise TypeError(type(v).__name__)
        Path(path).write_text(json.dumps(obj,indent=2,default=serialize),encoding='utf-8')
        self.status_var.set(f'Fused v14.6.3 JSON saved: {Path(path).name}')

    def _export_csv(self):
        if not self.engines:
            messagebox.showwarning('No results','Measure at least one view first.');return
        path=filedialog.asksaveasfilename(initialfile='three_view_columns_v1463.csv',
            defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        with open(path,'w',newline='',encoding='utf-8') as handle:
            writer=csv.writer(handle)
            writer.writerow(['view','method','column_index','valid_subpixel_edge',
                'included_in_fusion','top_y_roi_px','bottom_y_roi_px','raw_vertical_width_px',
                'raw_width_mm_if_calibrated','fused_result','fused_unit','calibration_status',
                'accuracy_validated'])
            for n,e in self.engines.items():
                m=self.view_results[n];f=m.get('fusion') or {}
                included=set(f.get('accepted_indices',[]))
                for i,ok in enumerate(e.valid):
                    mm=None
                    if ok and m.get('physical_stats') is not None:
                        if e.diameters_mm is not None:mm=float(e.diameters_mm[i])
                        elif m.get('scale_mm_per_px') is not None:
                            mm=float(e.widths_px[i]*m['scale_mm_per_px'])
                    writer.writerow([VIEW_LABELS[n],m.get('detection_method'),i,bool(ok),
                        i in included,float(e.top_edges[i]) if ok else '',
                        float(e.bottom_edges[i]) if ok else '',
                        float(e.widths_px[i]) if ok else '',mm,
                        f.get('value',''),f.get('unit',''),m.get('calibration_status'),False])
        self.status_var.set(f'40-column v14.6.3 CSV saved: {Path(path).name}')

    def _redraw(self):
        super()._redraw()
        # A stale yellow sample box should NOT look like an actively tracked
        # location. It is shown only while selecting the new sample.
        if not hasattr(self,'canvas') or self._seed_mode:return
        self.canvas.delete('seed_overlay')
        if self.image_cv is None or self._live_active or self.view_mode.get()!='original':return
        n=self._selected_name
        found=self.last_detected.get(n)
        if not found:return
        y=found.get('center_y')
        roi=found.get('measurement_roi_xyxy')
        if y is None or roi is None:return
        scale=self.display_scale;ox=self.display_offset_x;oy=self.display_offset_y
        if scale<=0:return
        self.canvas.create_line(ox+roi[0]*scale,oy+y*scale,ox+roi[2]*scale,oy+y*scale,
            fill='#d946ef',width=2, tags='v1463_track')


if __name__=='__main__':
    DynamicFusionThreeViewUI().mainloop()

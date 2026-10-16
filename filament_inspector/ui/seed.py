#!/usr/bin/env python3
"""Filament Inspector v14.6.2 — optional seed-guided, three-view GRAY8 detection.

This is an extension of the original v14.6.1 GUI. The old gradient/silhouette
paths and all previous calibration profiles/layouts remain available unchanged.
"""
from __future__ import annotations

from filament_inspector.paths import DATA_ROOT
from pathlib import Path
import json
import csv
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import cv2
import numpy as np
from PIL import Image,ImageTk,ImageDraw

from filament_inspector.ui.direct_calibration import ThreeViewCalibratedUI
from filament_inspector.ui.three_view import ThreeViewUI,PALETTE
from filament_inspector.algorithms.views import VIEW_NAMES, VIEW_LABELS, calibration_status
from filament_inspector.algorithms.seed import (
    SCHEMA, SeedDetectionError, sample_record, build_model, measure_seeded,
)


class SeedGuidedThreeViewUI(ThreeViewCalibratedUI):
    def _build_ui(self):
        self._seed_mode=False
        self._seed_drag=None
        self._seed_freeze_live=False
        self._seed_failure_diagnostics={}
        self._seed_failure_reasons={}
        self._seed_temp_samples={n:[] for n in VIEW_NAMES}
        self._seed_waiting_capture=False
        self._seed_capture_timeout_id=None
        self._seed_poll_id=None
        self._seed_pending_view=None
        super()._build_ui()
        self.title('Filament Inspector v14.6.2.2 | FRESH single-seed selection')
        # v14.6.1 labels live inside nested frames and were not updated by the
        # v14.6.2 title patch (the screenshot misleadingly said v14.6.1).
        for parent in (self.adv_button.master,self.canvas.master):
            for widget in parent.winfo_children():
                stack=[widget]
                while stack:
                    child=stack.pop()
                    if isinstance(child,ttk.Label):
                        old=str(child.cget('text'))
                        if 'Three-view Inspection | v14.6.1' in old:
                            child.configure(text='Three-view Inspection | v14.6.2.2 FRESH SINGLE-SEED')
                        elif old=='Filament Inspector v14.6.1':
                            child.configure(text='Filament Inspector v14.6.2.2')
                    stack.extend(child.winfo_children())
        for w in self.adv_button.master.grid_slaves(row=0,column=0):
            if isinstance(w,ttk.Label):w.configure(text='Filament Inspector v14.6.2.2')
        self.live_controls.capture_dir=DATA_ROOT/'captures_live_v1462'
        # Wrap the existing asynchronous full-resolution PNG download handler.
        # This preserves the v14.5 Pi protocol, metadata, validation and save
        # path; 'seed' is an independent capture type (never auto-measures).
        self._normal_live_capture_handler=self.live_controls._handle_capture
        self.live_controls._handle_capture=self._handle_capture_with_single_seed
        inspect=self.v142_rod_button.master
        sep=ttk.Separator(inspect,orient='horizontal')
        sep.grid(row=12,column=0,sticky='ew',pady=(9,7))
        ttk.Label(inspect,text='SEED-GUIDED GRAY8 (each view separately)',foreground='#2563eb').grid(
            row=13,column=0,sticky='w',pady=(0,3))
        self.seed_note=tk.StringVar(value='Choose a view, click Select to CAPTURE A NEW frame, then drag ONE interior rectangle.')
        self.seed_button=ttk.Button(inspect,text='Select ONE Sample (CAPTURE NEW PNG)',command=self._start_seed_selection)
        self.seed_button.grid(row=14,column=0,sticky='ew',pady=2)
        row=ttk.Frame(inspect);row.grid(row=15,column=0,sticky='ew')
        row.columnconfigure(0,weight=1);row.columnconfigure(1,weight=1)
        ttk.Button(row,text='Finish sample (optional)',command=self._finish_seed_selection).grid(row=0,column=0,sticky='ew',padx=(0,3))
        ttk.Button(row,text='Clear selected view',command=self._clear_seed_model).grid(row=0,column=1,sticky='ew',padx=(3,0))
        ttk.Button(inspect,text='Show Seed Mask / Failure Diagnostic',command=self._show_seed_diagnostic).grid(
            row=16,column=0,sticky='ew',pady=(4,2))
        ttk.Label(inspect,textvariable=self.seed_note,wraplength=315,justify='left').grid(
            row=17,column=0,sticky='ew',pady=(2,3))
        ttk.Label(inspect,text='Click Select to capture a NEW full-resolution PNG from the Pi. '
                             'Samples guide LOCATION; edges are measured on original grayscale with 50% crossing. '
                             'This cannot infer filament colour.',wraplength=315,justify='left').grid(
            row=18,column=0,sticky='w')
        self._bind_controls_wheel_recursive(inspect)
        self._refresh_seed_note()

    def _refresh_fields(self):
        super()._refresh_fields()
        if hasattr(self,'seed_note'):self._refresh_seed_note()

    def _refresh_seed_note(self):
        if not hasattr(self,'views') or not hasattr(self,'seed_note'):return
        n=self._selected_name
        m=self.views[n].get('seed_gray_model')
        temp=self._seed_temp_samples.get(n,[])
        if self._seed_mode and n==self._seed_active_view:
            self.seed_note.set(f'{VIEW_LABELS[n]}: drag ONE small rectangle entirely INSIDE the filament. Mouse release saves it automatically. Zoom/wheel works.')
        elif m:
            self.seed_note.set(f'{VIEW_LABELS[n]}: seed mode ACTIVE | {len(m["samples"])} samples | median={m["median"]:.1f}, MAD={m["mad"]:.1f} (GRAY8). Saved with ROI layout.')
        else:
            self.seed_note.set(f'{VIEW_LABELS[n]}: legacy detection; no seed model. Click Select to capture a NEW full-res frame and choose ONE rectangle.')

    def _seed_acquisition(self):
        path=getattr(self,'image_path',None)
        if not path:return None
        p=Path(path).with_suffix('.json')
        if not p.is_file():return None
        try:
            raw=json.loads(p.read_text(encoding='utf-8'))
            return {k:raw[k] for k in ('focus_epoch','settings_epoch','session','lens_position','exposure_us','analogue_gain') if k in raw}
        except (OSError,ValueError,TypeError):return None

    def _handle_capture_with_single_seed(self,kind,blob,headers):
        """Called by the existing integrated camera polling thread on Tk main loop.

        The parent handler decodes/validates the *new* 4608x2592 gray8 PNG,
        writes acquisition JSON and loads the freshly captured image first.
        Only then do we enter rectangle selection.
        """
        self._normal_live_capture_handler(kind,blob,headers)
        if kind=='seed' and self._seed_waiting_capture:
            self._single_seed_snapshot_ready()

    def _cancel_seed_capture_timeout(self):
        timer=self._seed_capture_timeout_id
        if timer is not None:
            try:self.after_cancel(timer)
            except (tk.TclError,ValueError):pass
            self._seed_capture_timeout_id=None

    def _seed_capture_timed_out(self):
        # The standard camera widget displays actual HTTP errors in info_text;
        # we only reset this button state when no completion callback arrived.
        if not self._seed_waiting_capture:return
        self._seed_waiting_capture=False
        self._seed_capture_timeout_id=None
        self._seed_pending_view=None
        if self._seed_poll_id is not None:
            try:self.after_cancel(self._seed_poll_id)
            except (tk.TclError,ValueError):pass
            self._seed_poll_id=None
        self.seed_button.configure(state='normal')
        self.seed_note.set('Fresh sample capture failed/timed out. Check camera status and SSH tunnel, then retry.')
        self.v14_hint.set('Sample capture failed: NO cached image was used.')

    def _check_seed_capture_failure(self):
        self._seed_poll_id=None
        if not self._seed_waiting_capture:return
        if not self.live_controls.capture_inflight:
            info=str(self.live_controls.info_text.get())
            self._cancel_seed_capture_timeout()
            self._seed_waiting_capture=False
            self._seed_pending_view=None
            self.seed_button.configure(state='normal')
            self.seed_note.set('NEW capture failed; stale image was NOT used. '+info)
            self.v14_hint.set('Seed capture error: '+info)
            return
        self._seed_poll_id=self.after(350,self._check_seed_capture_failure)

    def _start_seed_selection(self):
        if self._direct_job_running:
            messagebox.showwarning('Busy','Finish Direct rod calibration first.');return
        if self._seed_waiting_capture:return
        if self.views[self._selected_name].get('search_roi_xyxy') is None:
            messagebox.showwarning('No ROI','Set the selected view search ROI first.');return
        if getattr(self.live_controls,'continuous',None) is not None and self.live_controls.continuous.get():
            messagebox.showwarning('Continuous capture','Stop Continuous Measure to select samples.');return
        if self.live_controls.capture_inflight:
            messagebox.showwarning('Capture in progress','Finish current capture first.');return
        if self._seed_mode:self._cancel_seed_selection()
        self._seed_pending_view=self._selected_name
        self._seed_waiting_capture=True
        self.seed_button.configure(state='disabled')
        self.seed_note.set('Capturing NEW full-resolution 4608x2592 GRAY8 PNG now… Please wait.')
        self.v14_hint.set('Wait for NEW full-res snapshot before drawing. Cached photos are never used for sample selection.')
        self._seed_capture_timeout_id=self.after(68000,self._seed_capture_timed_out)
        self.live_controls._capture('seed')
        self._seed_poll_id=self.after(350,self._check_seed_capture_failure)

    def _single_seed_snapshot_ready(self):
        self._cancel_seed_capture_timeout()
        if self._seed_poll_id is not None:
            try:self.after_cancel(self._seed_poll_id)
            except (tk.TclError,ValueError):pass
            self._seed_poll_id=None
        self._seed_waiting_capture=False
        self.seed_button.configure(state='normal')
        n=self._seed_pending_view
        self._seed_pending_view=None
        if n not in self.views:return
        if self.image_cv is None or self.image_cv_raw is None:
            self.seed_note.set('New capture not loaded; please retry.');return
        self._seed_active_view=n
        self._seed_temp_samples[n]=[]
        self._seed_mode=True
        self._seed_freeze_live=bool(self._live_active)
        self._live_active=False  # Show the NEW full-resolution snapshot.
        self.view_mode.set('original')
        self.v14_mode='seed'
        self.tool_mode.set('point')
        self.canvas.configure(cursor='crosshair')
        self._refresh_seed_note()
        self.v14_hint.set('NEW FULL-RES GRAY8 acquired: draw ONE small rectangle inside the filament; release to save.')
        self._redraw()

    def _cancel_seed_selection(self):
        self._seed_mode=False
        self._seed_drag=None
        self.v14_mode='pan'
        self.tool_mode.set('pan')
        self.canvas.configure(cursor='')
        self.pending_box=None
        if self._seed_freeze_live:self._live_active=True
        self._seed_freeze_live=False
        self._redraw()

    def _finish_seed_selection(self):
        if not self._seed_mode:return
        n=self._seed_active_view
        samples=self._seed_temp_samples[n]
        if len(samples)!=1:
            messagebox.showwarning('Need ONE sample','Draw exactly ONE interior rectangle.');return
        try:
            # Single seed is an interior anchor, NEVER a diameter estimate.
            # Reject a large patch that could straddle background and filament.
            y0,y1=samples[0]['rect_xyxy'][1],samples[0]['rect_xyxy'][3]
            span=y1-y0+1
            approx=float(self.views[n].get('approx_expected_width_px',75.0))
            if approx>=8 and span>0.60*approx:
                raise ValueError(
                    f'Sample height {span} px is too large for the estimated '
                    f'{approx:.0f} px filament. Draw a smaller rectangle completely INSIDE it.')
            model=build_model(samples,image_shape=self.image_cv.shape[:2],acquisition=self._seed_acquisition())
            # Store intensities separately from geometric calibration, per view.
            self.views[n]['seed_gray_model']=model
            self.views[n]['previous_y']=None
            self._seed_failure_diagnostics.pop(n,None)
            self._seed_failure_reasons.pop(n,None)
            self.last_detected.pop(n,None);self.engines.pop(n,None);self.view_results.pop(n,None)
            self._reset_results_table()
            self._cancel_seed_selection();self._refresh_seed_note()
            self.v14_hint.set(f'{VIEW_LABELS[n]}: ONE seed accepted from NEW full-res frame. Capture + MEASURE; open diagnostic if failed.')
        except Exception as exc:
            # Permit redrawing ONE smaller box on the same fresh frame.
            self._seed_temp_samples[n]=[]
            self._refresh_seed_note();self._redraw()
            messagebox.showerror('Invalid seed model',str(exc))

    def _clear_seed_model(self):
        n=self._selected_name
        if self._seed_mode:self._cancel_seed_selection()
        self.views[n]['seed_gray_model']=None
        self._seed_temp_samples[n]=[]
        self._seed_failure_diagnostics.pop(n,None)
        self._seed_failure_reasons.pop(n,None)
        self.last_detected.pop(n,None);self.engines.pop(n,None);self.view_results.pop(n,None)
        self._reset_results_table();self._refresh_seed_note();self._redraw()

    def _v14_press(self,event):
        if not self._seed_mode:return super()._v14_press(event)
        p=self._canvas_to_image(event.x,event.y)
        if p is None:return
        self._seed_drag=p
        self.pending_box=(p,p)
        self._draw_pending(p,p)

    def _v14_drag_motion(self,event):
        if not self._seed_mode:return super()._v14_drag_motion(event)
        if self._seed_drag is None:return
        p=self._canvas_to_image(event.x,event.y)
        if p is None:return
        self.pending_box=(self._seed_drag,p)
        self._draw_pending(*self.pending_box)

    def _v14_release(self,event):
        if not self._seed_mode:return super()._v14_release(event)
        if self._seed_drag is None:return
        p=self._canvas_to_image(event.x,event.y)
        self.pending_box=None
        try:
            if p is None:raise ValueError('End rectangle inside image')
            n=self._seed_active_view
            old=self._seed_temp_samples[n]
            if len(old)>=1:raise ValueError('Only ONE rectangle per view; select again to replace it')
            ax,ay=self._seed_drag
            rect=[int(round(min(ax,p[0]))),int(round(min(ay,p[1]))),
                  int(round(max(ax,p[0]))),int(round(max(ay,p[1])))]
            sample=sample_record(self.image_cv,rect,self.views[n]['search_roi_xyxy'])
            old.append(sample)
            self._refresh_seed_note()
            self.after_idle(self._finish_seed_selection)  # auto-finish ONE sample after mouse release
        except ValueError as exc:
            messagebox.showwarning('Invalid interior sample',str(exc))
        finally:
            self._seed_drag=None
            self._redraw()

    def _enable_pan(self):
        if self._seed_mode:self._cancel_seed_selection()
        super()._enable_pan()

    def _enable_draw(self):
        if self._seed_mode:self._cancel_seed_selection()
        super()._enable_draw()

    def _redraw(self):
        super()._redraw()
        if not hasattr(self,'canvas') or self.image_cv is None or self.view_mode.get()!='original':return
        if self._seed_mode and not self._live_active:
            n=self._seed_active_view
            samples=self._seed_temp_samples[n]
        else:
            n=self._selected_name
            model=self.views[n].get('seed_gray_model') if hasattr(self,'views') else None
            samples=model['samples'] if model else []
        s,ox,oy=self.display_scale,self.display_offset_x,self.display_offset_y
        if s<=0:return
        for i,sample in enumerate(samples):
            x0,y0,x1,y1=sample['rect_xyxy']
            self.canvas.create_rectangle(ox+x0*s,oy+y0*s,ox+x1*s,oy+y1*s,outline='#facc15',width=2,
                                         tags='seed_overlay')
            self.canvas.create_text(ox+x0*s,oy+y0*s-5,text=f'S{i+1}',fill='#facc15',anchor='sw',tags='seed_overlay')

    def _draw_canvas_overlays(self):
        super()._draw_canvas_overlays()
        # v14.5 live video calls this overlay method independently of _redraw().
        if not self._seed_mode or self._live_active:return
        self.canvas.delete('seed_overlay')
        n=self._seed_active_view
        for i,sample in enumerate(self._seed_temp_samples[n]):
            x0,y0,x1,y1=sample['rect_xyxy']
            s,ox,oy=self.display_scale,self.display_offset_x,self.display_offset_y
            self.canvas.create_rectangle(ox+x0*s,oy+y0*s,ox+x1*s,oy+y1*s,
                                         outline='#facc15',width=2,tags='seed_overlay')

    def _process_one(self,n):
        v=self.views[n]
        if not v.get('seed_gray_model'):
            return super()._process_one(n)
        self._seed_failure_diagnostics.pop(n,None)
        self._seed_failure_reasons.pop(n,None)
        try:
            self._require_photos()
            if not v.get('search_roi_xyxy'):raise ValueError('Search ROI missing')
            # Keep v14.6.1's Direct focus / X lock on H × K_ref measurements.
            if n=='direct':
                profile=v.get('profile') or {}
                rec=((profile.get('target_plane_reference_correction') or {}).get('record') or {})
                correction=profile.get('target_plane_reference_correction') or {}
                if correction.get('enabled') and rec.get('schema')=='direct_reference_multiframe_v1461':
                    focus=float(self.live_controls.lens.get())
                    refroi=rec['search_roi_xyxy_at_calibration']
                    search=v['search_roi_xyxy']
                    if (abs(focus-float(rec['lens_position_commanded']))>0.005 or
                        (search[0],search[2])!=(refroi[0],refroi[2])):
                        raise ValueError('Direct H×K_ref focus / fixed-X mismatch; restore reference calibration conditions')
            found,m,e=measure_seeded(self.image_cv,self.background_cv,self.image_cv_raw.shape,v,
                                     acquisition=self._seed_acquisition())
            v['previous_y']=found['center_y']
            self.last_detected[n]=found
            self.view_results[n]=m
            self.engines[n]=e
            ThreeViewUI._put_success(self,n,m)  # don't mislabel as v14.1 fixed-polarity gradient
            values=list(self.result_table.item(n,'values'))
            values[1]+=' · SEEDED'
            self.result_table.item(n,values=values)
        except Exception as exc:
            self.last_detected.pop(n,None)
            if isinstance(exc,SeedDetectionError) and exc.diagnostic:
                self._seed_failure_diagnostics[n]=exc.diagnostic
            self._seed_failure_reasons[n]=str(exc)
            if n not in self._seed_failure_diagnostics:
                self._seed_failure_diagnostics[n]=self._fallback_seed_diagnostic(n)
            if self._seed_failure_diagnostics[n] is not None:
                self._seed_failure_diagnostics[n]['failure_reason']=str(exc)
            self._put_failure(n,'SEED FAILED',str(exc))

    def _fallback_seed_diagnostic(self,n):
        """Allow inspecting ORIGINAL/BG/diff even if calibration, sample
        validation, or seed localization failed before producing a mask.
        This is observational ONLY, not a fabricated detection result.
        """
        if self.image_cv is None or self.background_cv is None:return None
        view=self.views[n]
        box=view.get('search_roi_xyxy')
        if box is None:return None
        x0,y0,x1,y1=map(int,box)
        c=self.image_cv;b=self.background_cv
        if c.shape!=b.shape or not(0<=x0<x1<c.shape[1] and 0<=y0<y1<c.shape[0]):return None
        cur=cv2.cvtColor(c[y0:y1+1,x0:x1+1],cv2.COLOR_BGR2GRAY)
        bg=cv2.cvtColor(b[y0:y1+1,x0:x1+1],cv2.COLOR_BGR2GRAY)
        d=cv2.GaussianBlur(np.abs(cur.astype(np.float32)-bg.astype(np.float32)),(3,3),0)
        threshold=max(5.,float(view.get('foreground_threshold',14.)))
        mask=(d>threshold).astype(np.uint8)
        model=view.get('seed_gray_model') or {}
        return {'search_xyxy':list(box),'difference':d,'foreground_mask':mask,
                'threshold':threshold,'seed_rects':[s['rect_xyxy'] for s in model.get('samples',[])],
                'stage':'PRECHECK FAILURE — raw abs-diff preview (not validated contour)'}

    def _v14_process_all(self):
        super()._v14_process_all()
        if self._seed_failure_reasons:
            reasons=' | '.join(f'{VIEW_LABELS[n]}: {reason}' for n,reason in self._seed_failure_reasons.items())
            self.v14_hint.set('SEED FAILED — '+reasons[:700]+' | Open Seed Diagnostic.')

    def process_selected(self):
        super().process_selected()
        n=self._selected_name
        reason=self._seed_failure_reasons.get(n)
        if reason:
            self.v14_hint.set(f'{VIEW_LABELS[n]} | SEED FAILED: {reason} | Open Show Seed Mask / Failure Diagnostic.')

    def process_all(self):
        super().process_all()
        reasons=[f'{VIEW_LABELS[n]}: {msg}' for n,msg in self._seed_failure_reasons.items()]
        if reasons:
            self.v14_hint.set('SEED FAILURE: '+' | '.join(reasons))
            self.detail_var.set('SEED FAILURE DETAILS: '+' | '.join(reasons))

    def _show_seed_diagnostic(self):
        if self.image_cv is None or self.background_cv is None:
            messagebox.showwarning('Missing image','Capture filament + background photos first.');return
        n=self._selected_name
        found=self.last_detected.get(n)
        diag=self._seed_failure_diagnostics.get(n)
        if found and 'seed_diagnostic' in found:diag=found['seed_diagnostic']
        if diag is None:
            diag=self._fallback_seed_diagnostic(n)
        if diag is None:
            messagebox.showinfo('No seed diagnostic',
                'Load/capture matching filament and empty-background FULL-RES frames, then set this view ROI.');return
        search=diag['search_xyxy'];x0,y0,x1,y1=map(int,search)
        img=cv2.cvtColor(self.image_cv[y0:y1+1,x0:x1+1],cv2.COLOR_BGR2GRAY)
        bg=cv2.cvtColor(self.background_cv[y0:y1+1,x0:x1+1],cv2.COLOR_BGR2GRAY)
        diff=diag['difference'];mask=diag['foreground_mask']
        if mask.shape!=img.shape:messagebox.showerror('Diagnostic','Diagnostic data has mismatched shape');return
        def rgb(a):return np.stack([np.uint8(a)]*3,axis=2)
        panels=[rgb(img),rgb(bg),rgb(np.clip(diff*3,0,255)),rgb(mask*255)]
        guides=diag.get('guides')
        for rect in diag.get('seed_rects',[]):
            xa,ya,xb,yb=rect
            cv2.rectangle(panels[0],(xa-x0,ya-y0),(xb-x0,yb-y0),(40,230,255),1)
        if guides is not None:
            for i in range(min(len(guides),img.shape[1])):
                if np.all(np.isfinite(guides[i])):
                    for yy,color in ((guides[i,0],(0,250,60)),(guides[i,1],(250,150,30))):
                        yi=int(round(yy-y0))
                        if 0<=yi<img.shape[0]:panels[3][yi,i]=color
        engine=self.engines.get(n)
        result=self.view_results.get(n)
        if engine is not None and result is not None:
            roi=result['roi']
            for i,valid in enumerate(engine.valid):
                if not valid:continue
                for y,color in ((engine.top_edges[i],(0,255,0)),(engine.bottom_edges[i],(255,0,0))):
                    yi=int(round(roi[1]+y-y0))
                    if 0<=yi<img.shape[0]:panels[0][yi,i]=color
        # Vertical side-by-side columns are still native 40px each; 4x zoom
        # keeps every individual source pixel inspectable.
        vis=np.concatenate(panels,axis=1)
        pil=Image.fromarray(vis).resize((vis.shape[1]*4,vis.shape[0]*4),Image.Resampling.NEAREST)
        win=tk.Toplevel(self);win.title(f'v14.6.2 Seed Diagnostics — {VIEW_LABELS[n]}');win.geometry('930x760')
        info=(f'{VIEW_LABELS[n]} | 1: original GRAY8 (yellow=seeds, green/blue=measured edges) | '
              f'2: background | 3: abs-diff ×3 | 4: binary mask + guides | '
              f'threshold={diag.get("threshold",float("nan")):.1f} | '
              f'stage={diag.get("stage","unknown")} | '
              f'error={diag.get("failure_reason",self._seed_failure_reasons.get(n,"none"))}')
        ttk.Label(win,text=info,wraplength=880,justify='left').pack(anchor='w',padx=8,pady=8)
        model=self.views[n].get('seed_gray_model')
        if model:
            chart=tk.Canvas(win,height=105,width=820,bg='white',highlightthickness=1,
                            highlightbackground='#bbbbbb')
            chart.pack(fill='x',padx=12,pady=4)
            histogram=np.asarray(model['histogram_256_bins'],dtype=float)
            height=max(1,float(histogram.max()))
            for i,count in enumerate(histogram):
                x=22+i*3
                chart.create_line(x,82,x,82-68*count/height,fill='#475569',width=2)
            chart.create_text(22,96,text='0',anchor='w')
            chart.create_text(22+128*3,96,text='128',anchor='center')
            chart.create_text(22+255*3,96,text='255',anchor='e')
            chart.create_text(22,4,text=f'Foreground histogram | median={model["median"]:.1f}, MAD={model["mad"]:.1f}',anchor='nw')
        holder=tk.Frame(win);holder.pack(fill='both',expand=True)
        canvas=tk.Canvas(holder,bg='#121212');canvas.pack(side='left',fill='both',expand=True)
        vbar=ttk.Scrollbar(holder,orient='vertical',command=canvas.yview);vbar.pack(side='right',fill='y')
        hbar=ttk.Scrollbar(win,orient='horizontal',command=canvas.xview);hbar.pack(fill='x')
        canvas.configure(yscrollcommand=vbar.set,xscrollcommand=hbar.set)
        photo=ImageTk.PhotoImage(pil);canvas.create_image(0,0,image=photo,anchor='nw')
        canvas.image=photo;canvas.configure(scrollregion=(0,0,pil.width,pil.height))
        ttk.Button(win,text='Save diagnostic PNG + metadata',command=lambda:self._export_seed_diagnostic(pil,n,diag)).pack(pady=6)

    def _export_seed_diagnostic(self,pil,n,diag):
        path=filedialog.asksaveasfilename(defaultextension='.png',initialfile=f'{n}_seed_debug_v1462.png',filetypes=[('PNG','*.png')])
        if not path:return
        p=Path(path);pil.save(p)
        meta={'view':n,'stage':diag.get('stage'), 'threshold':diag.get('threshold'),
              'search_xyxy':diag['search_xyxy'],'seed_rects':diag.get('seed_rects'),
              'error_or_result':diag.get('failure_reason',self._seed_failure_reasons.get(n,self.detail_var.get()))}
        p.with_suffix('.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
        self.status_var.set(f'Seed diagnostic saved: {p.name}')

    def _selected_diagnostic(self):
        m=self.view_results.get(self._selected_name)
        if m is None or m.get('detection_method')!='seed_guided':
            return super()._selected_diagnostic()
        e=self.engines[self._selected_name]
        valid=np.flatnonzero(e.valid)
        if not len(valid):
            messagebox.showwarning('No valid slices','No accepted subpixel edges.');return
        xi=int(valid[np.argmin(abs(valid-(len(e.valid)-1)/2))])
        f=e.rectified_gray[:,xi].astype(float)
        win=tk.Toplevel(self);win.title('Seeded original gray8 50% crossing — '+VIEW_LABELS[self._selected_name])
        win.geometry('920x520')
        ttk.Label(win,text=f'Column {xi}: original GRAY8, polarity independent. '
                  f'Top={e.top_edges[xi]:.3f}px, bottom={e.bottom_edges[xi]:.3f}px, '
                  f'width={e.widths_px[xi]:.3f}px. Edge validity is not an accuracy guarantee.',
                  wraplength=840).pack(pady=8)
        can=tk.Canvas(win,bg='white');can.pack(fill='both',expand=True,padx=10,pady=10)
        def draw(_=None):
            can.delete('all')
            W=max(500,can.winfo_width());H=max(300,can.winfo_height())
            l,t=50,24;cw=W-80;ch=H-80
            X=lambda i:l+float(i)/max(1,len(f)-1)*cw
            Y=lambda intensity:t+ch-(float(intensity)/255.)*ch
            can.create_line(l,t,l,t+ch,l+cw,t+ch,fill='black')
            line=[]
            for i,v in enumerate(f):line.extend((X(i),Y(v)))
            can.create_line(*line,fill='#2563eb',width=2)
            for value,color,label in ((e.top_edges[xi],'#16a34a','TOP'),
                                      (e.bottom_edges[xi],'#0ea5e9','BOTTOM')):
                can.create_line(X(value),t,X(value),t+ch,fill=color,width=2,dash=(4,3))
                can.create_text(X(value)+3,t+16,text=f'{label} {value:.3f}',anchor='w',fill=color)
        can.bind('<Configure>',draw);win.after(40,draw)

    def _export_results(self):
        # v13 summary JSON cannot serialize v14.2/v14.6.2 binary diagnostic
        # arrays. Export small, audit-friendly results; raw masks are separately
        # available from Save diagnostic PNG.
        if not self.view_results:
            messagebox.showwarning('No results','Measure at least one view first.');return
        path=filedialog.asksaveasfilename(initialfile='three_view_results_v1462.json',
             defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        obj={'schema':'filament_three_view_results_v1462',
             'original_frame':str(self.image_path) if self.image_path else None,
             'background':str(self.background_path) if self.background_path else None,
             'working_rotation_degrees_clockwise':90,
             'metrology_note':'Subpixel 50% intensity crossing on original GRAY8; seed is localization only.',
             'confidence_note':'Detection-quality heuristic; NOT measurement accuracy.',
             'roundness':'NOT measured', 'views':{}}
        for n,m in self.view_results.items():
            found=self.last_detected.get(n,{})
            allowed=('measurement_roi_xyxy','search_xyxy','foreground_rough_edge_y',
                     'center_y','coarse_width_px','adaptive_threshold_gray',
                     'foreground_contrast','foreground_column_coverage',
                     'candidates','valid_guide_columns','seed_model_summary')
            obj['views'][n]={'localization':{k:found[k] for k in allowed if k in found},
                 'measurement':m,'calibration_status':m.get('calibration_status'),
                 'seed_sample_count':len((self.views[n].get('seed_gray_model') or {}).get('samples',[]))}
        def convert(value):
            if isinstance(value,np.ndarray):return value.tolist()
            if isinstance(value,np.generic):return value.item()
            raise TypeError(type(value).__name__)
        Path(path).write_text(json.dumps(obj,indent=2,default=convert),encoding='utf-8')
        self.status_var.set(f'Saved measurement summary: {Path(path).name}')

    def _export_csv(self):
        if not self.engines:
            messagebox.showwarning('No results','Measure at least one view first.');return
        path=filedialog.asksaveasfilename(initialfile='three_view_slices_v1462.csv',
             defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        with open(path,'w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f)
            writer.writerow(['view','detection_method','x_index_in_40px_band',
                            'valid','top_y_roi_px','bottom_y_roi_px','width_px',
                            'width_mm_if_calibrated','calibration_type'])
            for n,e in self.engines.items():
                m=self.view_results[n]
                for i,ok in enumerate(e.valid):
                    real_mm=None
                    if ok and m.get('physical_stats') is not None:
                        if e.diameters_mm is not None:
                            real_mm=float(e.diameters_mm[i])
                        elif m.get('scale_mm_per_px') is not None:
                            real_mm=float(e.widths_px[i]*m['scale_mm_per_px'])
                    kind=('H_x_K_ref' if n=='direct' else 'mirror_local_scale') if real_mm is not None else 'pixel_only'
                    writer.writerow([VIEW_LABELS[n],m.get('detection_method','gradient'),i,bool(ok),
                                     float(e.top_edges[i]) if ok else None,
                                     float(e.bottom_edges[i]) if ok else None,
                                     float(e.widths_px[i]) if ok else None,real_mm,kind])
        self.status_var.set(f'Saved per-slice measurements: {Path(path).name}')

    def _load_layout(self):
        if self._seed_waiting_capture:
            messagebox.showwarning('Capture in progress','Wait until new seed photo has been captured.');return
        if self._seed_mode:self._cancel_seed_selection()
        super()._load_layout()
        self._seed_failure_diagnostics.clear()
        if hasattr(self,'seed_note'):self._refresh_seed_note()

    def _selected_changed(self,event=None):
        if self._seed_waiting_capture:
            messagebox.showwarning('Capture in progress','Wait for fresh sample photo before changing views.');
            self.selected_name_var.set(VIEW_LABELS[self._seed_pending_view]);return
        if self._seed_mode:
            messagebox.showwarning('Finish selection','Finish or cancel sample selection before switching views.')
            self.selected_name_var.set(VIEW_LABELS[self._seed_active_view]);return
        super()._selected_changed(event)
        self._refresh_seed_note()

    def _table_selected(self,event=None):
        if self._seed_mode:return
        super()._table_selected(event)
        self._refresh_seed_note()


if __name__=='__main__':
    SeedGuidedThreeViewUI().mainloop()

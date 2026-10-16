#!/usr/bin/env python3
"""v14 simple three-view workflow: draw DIRECT once, Y-drag mirror boxes, measure.

Uses the UNMODIFIED v11 physical-edge estimator via v13 orchestration.
Background subtraction is used for localization only.  The X interval stays
identical across all three coarse search boxes; mirrors move vertically only.
"""
from __future__ import annotations

from filament_inspector.paths import DATA_ROOT
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np

from filament_inspector.ui.three_view import ThreeViewUI, PALETTE
from filament_inspector.algorithms.views import (
    VIEW_NAMES, VIEW_LABELS, new_view, normalize_profile, calibration_status,
)
from filament_inspector.algorithms.layout import (
    normalize_drag, move_roi_vertical, auto_generate_mirrors, validate_shared_x,
)


class SimpleThreeViewUI(ThreeViewUI):
    """Retain v13's measurement, calibration and diagnostic back end.

    The many v11-v13 controls are constructed for compatibility but hidden;
    only the simplified controls below are exposed.  No v11 algorithm changed.
    """

    def _build_ui(self):
        super()._build_ui()
        self.title("Filament Diameter v14 | Simple Three-view Measurement")
        self.geometry("1480x920")
        self.minsize(1000, 690)
        self.v14_mode = 'pan'                 # 'draw' or 'pan'; mirror drag always available
        self.v14_drag = None                 # {'which': 'direct'|'left'|'right',...}
        self.pending_box = None
        self.v14_original_shape = None
        self.v14_hint = tk.StringVar(value="1. Load photos.  2. Draw Direct search box.  3. Drag mirror boxes vertically.  4. Measure.")
        self.v14_photo_label = tk.StringVar(value="Current: not loaded\nBackground: not loaded")
        self.v14_cal_label = tk.StringVar(value="Direct: pixel only until profile loaded")
        self.v14_tool_text = tk.StringVar(value="Mode: PAN / drag mirror boxes vertically")
        self.v14_advanced_open = False

        # Hide all previous v11/v12/v13 control groups; keep inherited methods.
        panel = self.controls_frame
        for widget in panel.winfo_children():
            grid = widget.grid_info()
            if grid:
                widget.grid_remove()
        # Main display: hide legacy per-view radio switches (v14 uses one image).
        header = self.canvas.master.grid_slaves(row=0, column=0)
        if header:
            for widget in header[0].winfo_children():
                if isinstance(widget, ttk.Radiobutton):
                    widget.grid_remove()
                elif isinstance(widget, ttk.Label) and widget.cget('text') == 'Paper Method View':
                    widget.configure(text='Three-view Inspection | v14')

        panel.columnconfigure(0, weight=1)
        base = ttk.Frame(panel, padding=11)
        base.grid(row=0, column=0, sticky="new")
        base.columnconfigure(0, weight=1)
        ttk.Label(base, text="Filament Inspector  v14", font=("Helvetica", 18, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 7))
        ttk.Label(base, text="Draw once · Align mirrors · Measure", foreground="#555555").grid(
            row=1, column=0, sticky="w", pady=(0, 9))

        section = ttk.LabelFrame(base, text="1   Load images  /  加载照片", padding=9)
        section.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        ttk.Button(section, text="With filament  ·  有耗材照片", command=self._load_image_dialog).grid(
            row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(section, text="Empty background  ·  无耗材背景", command=self._load_background_dialog).grid(
            row=1, column=0, sticky="ew")
        ttk.Label(section, textvariable=self.v14_photo_label, wraplength=323, justify="left").grid(
            row=2, column=0, sticky="w", pady=(6, 0))

        section = ttk.LabelFrame(base, text="2   Search zones  /  搜索框", padding=9)
        section.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        self.draw_button = ttk.Button(section, text="① Draw DIRECT box  ·  框选中央耗材", command=self._enable_draw)
        self.draw_button.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(section, text="Regenerate mirror boxes  ·  重新生成镜面框",
                   command=self._regenerate_mirrors).grid(row=1, column=0, sticky="ew")
        ttk.Label(section, text=("Drag a narrow X-band covering the filament and background "
                   "above/below it. Mirror boxes share X; drag green/blue boxes UP/DOWN only."),
                  wraplength=320, justify="left").grid(row=2, column=0, sticky="w", pady=(6, 5))
        ttk.Label(section, textvariable=self.v14_tool_text, wraplength=315).grid(
            row=3, column=0, sticky="w")
        ttk.Button(section, text="Pan / Move image  ·  平移画面", command=self._enable_pan).grid(
            row=4, column=0, sticky="ew", pady=(5, 0))

        section = ttk.LabelFrame(base, text="3   Locate & measure  /  定位并测量", padding=9)
        section.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        ttk.Button(section, text="②  MEASURE ALL THREE VIEWS", command=self._v14_process_all).grid(
            row=0, column=0, sticky="ew", ipady=7)
        ttk.Label(section, textvariable=self.v14_hint, wraplength=322, justify="left").grid(
            row=1, column=0, sticky="w", pady=(6, 0))

        section = ttk.LabelFrame(base, text="4   Inspect  /  单视角诊断", padding=9)
        section.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, weight=1)
        sel = ttk.Combobox(section, textvariable=self.selected_name_var,
                           values=[VIEW_LABELS[n] for n in VIEW_NAMES], state="readonly")
        sel.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        sel.bind("<<ComboboxSelected>>", self._selected_changed)
        row = ttk.Frame(section)
        row.grid(row=1, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        ttk.Button(row, text="Measure one", command=self._v14_process_one).grid(
            row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(row, text="Slice Diagnostic", command=self._selected_diagnostic).grid(
            row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(section, textvariable=self.v14_cal_label, wraplength=320).grid(
            row=2, column=0, sticky="w", pady=(5, 0))

        row = ttk.Frame(base)
        row.grid(row=6, column=0, sticky="ew", pady=(1, 8))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        ttk.Button(row, text="Save ROI layout", command=self._save_layout).grid(
            row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(row, text="Load ROI layout", command=self._load_layout).grid(
            row=0, column=1, sticky="ew", padx=(3, 0))

        self.adv_button = ttk.Button(base, text="▶ Advanced calibration & settings",
                                     command=self._toggle_advanced)
        self.adv_button.grid(row=7, column=0, sticky="ew")
        self.advanced_panel = ttk.LabelFrame(base, text="Advanced  /  高级设置", padding=9)
        self.advanced_panel.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        self.advanced_panel.columnconfigure(0, weight=1)
        self.advanced_panel.columnconfigure(1, weight=1)
        adv = self.advanced_panel
        ttk.Label(adv, text="View-specific calibration — NEVER reuse Direct for mirrors",
                  wraplength=315).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(adv, text="Load calibration for selected view...", command=self._load_view_profile).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(adv, textvariable=self.profile_status_var, wraplength=320).grid(
            row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(adv, text="Polarity").grid(row=3, column=0, sticky="w")
        ttk.Combobox(adv, textvariable=self.polarity_var, state="readonly", width=11,
                     values=["bright", "dark"]).grid(row=3, column=1, sticky="e")
        ttk.Label(adv, text="Approximate width (px)").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(adv, from_=5, to=500, increment=1, width=10,
                    textvariable=self.approx_px_var).grid(row=4, column=1, sticky="e")
        ttk.Label(adv, text="Background threshold").grid(row=5, column=0, sticky="w")
        ttk.Spinbox(adv, from_=5, to=150, increment=1, width=10,
                    textvariable=self.foreground_var).grid(row=5, column=1, sticky="e")
        ttk.Label(adv, text="Measurement ROI padding (px)").grid(row=6, column=0, sticky="w")
        ttk.Spinbox(adv, from_=3, to=50, increment=1, width=10,
                    textvariable=self.padding_var).grid(row=6, column=1, sticky="e")
        ttk.Label(adv, text="Reference rod (mm)").grid(row=7, column=0, sticky="w")
        ttk.Spinbox(adv, from_=0.1, to=20, increment=.001, width=10,
                    textvariable=self.known_width_var).grid(row=7, column=1, sticky="e")
        ttk.Button(adv, text="Calibrate selected K_ref with reference rod", command=self._calibrate_rod).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Button(adv, text="Save selected calibration JSON", command=self._export_profile).grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        ttk.Button(adv, text="Export three-view summary JSON", command=self._export_results).grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Button(adv, text="Export per-slice CSV", command=self._export_csv).grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        ttk.Label(adv, text="Confidence is a detection-quality heuristic, not a probability or accuracy estimate.",
                  wraplength=315).grid(row=12, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.advanced_panel.grid_remove()
        self._bind_controls_wheel_recursive(base)
        self._load_bundled_direct_calibration()
        self._refresh_v14_status()

    def _bind_events(self):
        super()._bind_events()
        # The v14 simple canvas interaction REPLACES the v11 four-point tool.
        self.canvas.bind("<ButtonPress-1>", self._v14_press)
        self.canvas.bind("<B1-Motion>", self._v14_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._v14_release)

    def _load_bundled_direct_calibration(self):
        path = DATA_ROOT / "direct_view_calibration.json"
        if not path.exists():
            return
        try:
            profile = normalize_profile(json.loads(path.read_text(encoding="utf-8")), "direct")
            self._assert_independent(profile, "direct")
            self.views["direct"]["profile"] = profile
            self.views["direct"]["profile_path"] = str(path)
            self._refresh_fields()
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self.status_var.set(f"Bundled Direct calibration was not loaded: {exc}")

    def _refresh_v14_status(self):
        if not hasattr(self, 'v14_hint'):
            return
        p = self.image_path.name if self.image_path else "not loaded"
        b = self.background_path.name if self.background_path else "not loaded"
        self.v14_photo_label.set(f"Current: {p}\nBackground: {b}")
        selected = self.views[self._selected_name]
        label = calibration_status(selected['profile'])
        if selected['profile'] is not None and self.image_cv_raw is not None:
            cal = selected['profile']['local_planar_calibration']
            expected = (int(cal['original_image_height_px']), int(cal['original_image_width_px']))
            if self.image_cv_raw.shape[:2] != expected:
                label = 'RESOLUTION MISMATCH (pixel only)'
        self.v14_cal_label.set(f"{VIEW_LABELS[self._selected_name]}: {label}")
        self.v14_tool_text.set("Mode: DRAW CENTRAL rectangle" if self.v14_mode == 'draw'
                               else "Mode: PAN | Green/blue boxes: vertical drag")

    def _selected_changed(self, event=None):
        super()._selected_changed(event)
        self._refresh_v14_status()

    def _load_view_profile(self):
        super()._load_view_profile()
        self._refresh_v14_status()

    def _load_image(self, path):
        previous = self.v14_original_shape if hasattr(self,'v14_original_shape') else None
        super()._load_image(path)
        if not hasattr(self, 'views'):
            return
        if self.image_cv_raw is None:
            return
        new_shape = tuple(self.image_cv_raw.shape[:2])
        if previous is not None and previous != new_shape:
            for v in self.views.values():
                v['search_roi_xyxy'] = None
                v['previous_y'] = None
            self.v14_hint.set("Resolution changed. Draw a new Direct ROI; previous layout cleared.")
        self.v14_original_shape = new_shape
        factor = float(new_shape[1]) / 4608.0
        for key, view in self.views.items():
            if view['search_roi_xyxy'] is None:
                view['approx_expected_width_px'] = (74.0 if key == 'direct' else 62.0) * factor
        self._refresh_fields()
        self._refresh_v14_status()
        self.view_mode.set('original')
        self._redraw()

    def _load_background_dialog(self):
        super()._load_background_dialog()
        self._refresh_v14_status()

    def _current_display_pil(self):
        # Deliberately suppress v13's full-image PIL annotation; vector overlays
        # on the Tk canvas permit smooth ROI dragging at any zoom level.
        from filament_inspector.ui.background import BackgroundTrackingUI
        return BackgroundTrackingUI._current_display_pil(self)

    def _redraw(self):
        super(ThreeViewUI, self)._redraw()  # v11 viewport-cropped image renderer
        if hasattr(self, 'views') and hasattr(self, 'canvas'):
            self._draw_canvas_overlays()

    def _draw_canvas_overlays(self):
        c = self.canvas
        c.delete("v14_overlay")
        if self.image_cv is None or self.view_mode.get() != 'original':
            return
        s, ox, oy = self.display_scale, self.display_offset_x, self.display_offset_y
        if s <= 0:
            return
        for key in VIEW_NAMES:
            rect = self.views[key]['search_roi_xyxy']
            if not rect:
                continue
            x0,y0,x1,y1 = rect
            px0, py0, px1, py1 = (ox+x0*s, oy+y0*s, ox+x1*s, oy+y1*s)
            if px1 < 0 or py1 < 0 or px0 > c.winfo_width() or py0 > c.winfo_height():
                continue
            color = PALETTE[key]
            c.create_rectangle(px0,py0,px1,py1,outline=color,width=3,
                               tags=('v14_overlay', f'v14_{key}'))
            title = VIEW_LABELS[key] + (" · ↕" if key!='direct' else "")
            c.create_text(px0+3,max(py0-11,10),text=title,anchor='sw',fill=color,
                          font=('Helvetica',11,'bold'),tags=('v14_overlay', f'v14_{key}'))
            found = self.last_detected.get(key)
            if found:
                # Show the ACTUAL v11 ROI if v13 tightened the background margin.
                used = self.view_results.get(key, {}).get('roi', found['measurement_roi_xyxy'])
                a,b,d,e = used
                c.create_rectangle(ox+a*s,oy+b*s,ox+d*s,oy+e*s,
                                   outline='#ef42c8',width=2,dash=(4,2),
                                   tags=('v14_overlay', f'v14_{key}'))
        if self.pending_box:
            a,b = self.pending_box
            self._draw_pending(a,b)

    def _draw_pending(self,a,b):
        self.canvas.delete('v14_pending')
        if a is None or b is None:
            return
        s, ox, oy = self.display_scale,self.display_offset_x,self.display_offset_y
        x0,x1 = sorted((a[0],b[0]));y0,y1=sorted((a[1],b[1]))
        self.canvas.create_rectangle(ox+x0*s,oy+y0*s,ox+x1*s,oy+y1*s,
                  outline=PALETTE['direct'],width=3,dash=(5,3),tags=('v14_overlay','v14_pending'))

    def _enable_draw(self):
        if self.image_cv is None:
            messagebox.showwarning('Load image first', 'Load a WITH-filament image before drawing the Direct ROI.')
            return
        self.v14_mode = 'draw'
        self.tool_mode.set('point')
        self.canvas.configure(cursor='crosshair')
        self.v14_hint.set('Click and DRAG a box around the Direct filament. Include background above and below it.')
        self._refresh_v14_status()

    def _enable_pan(self):
        self.v14_mode='pan'
        self.tool_mode.set('pan')
        self._refresh_v14_status()
        self._redraw()

    def _mirror_at(self, x, y):
        """Select the nearest mirror box containing the click; Direct isn't movable."""
        options=[]
        for key in ('left','right'):
            rect=self.views[key]['search_roi_xyxy']
            if rect and rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                options.append((abs(y-(rect[1]+rect[3])*0.5),key))
        return min(options)[1] if options else None

    def _v14_press(self, event):
        if self.image_cv is None:
            return
        p=self._canvas_to_image(event.x,event.y)
        if p is None:
            return
        if self.v14_mode=='draw':
            self.v14_drag={'which':'direct', 'anchor':p}
            self.pending_box=(p,p)
            self._draw_pending(p,p)
            return
        key=self._mirror_at(*p)
        if key:
            self.v14_drag={'which':key,'anchor':p,'initial':list(self.views[key]['search_roi_xyxy'])}
            return
        # Pan elsewhere using the unchanged v11 viewport transform.
        self.tool_mode.set('pan')
        from filament_inspector.ui.base import PaperMethodDiameterUI
        PaperMethodDiameterUI._canvas_press(self,event)
        self.v14_drag={'which':'pan'}

    def _v14_drag_motion(self, event):
        if self.v14_drag is None:
            return
        which=self.v14_drag['which']
        if which=='pan':
            from filament_inspector.ui.base import PaperMethodDiameterUI
            PaperMethodDiameterUI._canvas_drag(self,event)
            return
        p=self._canvas_to_image(event.x,event.y)
        if p is None:
            return
        if which=='direct':
            self.pending_box=(self.v14_drag['anchor'],p)
            self._draw_pending(*self.pending_box)
        else:
            dy=p[1]-self.v14_drag['anchor'][1]
            self.views[which]['search_roi_xyxy']=move_roi_vertical(
                self.v14_drag['initial'],dy,self.image_cv.shape)
            self.last_detected.pop(which,None)
            self.view_results.pop(which,None)
            self.engines.pop(which,None)
            self._draw_canvas_overlays()     # vector redraw: no full-frame PIL resize

    def _v14_release(self, event):
        if not self.v14_drag:
            return
        mode=self.v14_drag['which']
        if mode=='direct':
            p=self._canvas_to_image(event.x,event.y)
            if p is not None:
                try:
                    rect=normalize_drag(self.v14_drag['anchor'],p,self.image_cv.shape)
                    self._apply_direct_roi(rect)
                except ValueError as exc:
                    messagebox.showwarning('Direct search box',str(exc))
            self.pending_box=None
            self.v14_mode='pan'
            self.tool_mode.set('pan')
            self._refresh_v14_status()
        elif mode in ('left','right'):
            self.v14_hint.set(f'{VIEW_LABELS[mode]} moved vertically. Click MEASURE ALL THREE VIEWS.')
            self._reset_results_table()
        else:
            from filament_inspector.ui.base import PaperMethodDiameterUI
            PaperMethodDiameterUI._canvas_release(self,event)
        self.v14_drag=None
        self._redraw()

    def _apply_direct_roi(self,rect):
        self.views['direct']['search_roi_xyxy']=list(rect)
        self._regenerate_mirrors(confirm=False)
        self.last_detected.clear();self.view_results.clear();self.engines.clear()
        for v in self.views.values():v['previous_y']=None
        self._reset_results_table()
        self.v14_hint.set('Generated mirror boxes. Drag GREEN/BLUE boxes vertically to the two mirror filament lines, then measure.')

    def _regenerate_mirrors(self,confirm=True):
        rect=self.views['direct']['search_roi_xyxy']
        if rect is None:
            if confirm: messagebox.showwarning('Draw Direct first','Draw the Direct View search box first.')
            return
        try:
            proposal=auto_generate_mirrors(rect,self.image_cv.shape)
            for n in VIEW_NAMES:
                self.views[n]['search_roi_xyxy']=proposal[n]
                self.views[n]['previous_y']=None
            self.view_results.clear();self.engines.clear();self.last_detected.clear()
            self._reset_results_table()
            self._redraw()
            if confirm:self.v14_hint.set('Mirrors regenerated. Drag GREEN/BLUE search zones up/down to align with the visible mirror filaments.')
        except ValueError as exc:
            messagebox.showerror('Cannot generate mirror boxes',str(exc))

    def _v14_process_one(self):
        self._commit_fields()
        self.process_selected()
        vals=self.result_table.item(self._selected_name,'values')
        self.v14_hint.set(f'{VIEW_LABELS[self._selected_name]}: {vals[1]}. See results at bottom.')

    def _v14_process_all(self):
        if not all(self.views[key]['search_roi_xyxy'] for key in VIEW_NAMES):
            messagebox.showwarning('Search boxes missing','First DRAW Direct ROI; mirror search zones are generated automatically.')
            return
        try:
            validate_shared_x({n:v['search_roi_xyxy'] for n,v in self.views.items()},self.image_cv.shape)
            self._require_photos()
        except Exception as exc:
            messagebox.showwarning('Setup incomplete',str(exc))
            return
        self._commit_fields()
        self.process_all()
        ok=[n for n in VIEW_NAMES if n in self.view_results]
        if len(ok) == 3:
            self.v14_hint.set('All 3 views measured. Inspect results below; mirror mm requires independent calibration.')
        else:
            self.v14_hint.set(f'{len(ok)}/3 views measured. Drag failed mirror box vertically, check polarity/background, and retry.')

    def _selected_diagnostic(self):
        """v11 Slice Diagnostic, but NEVER label uncalibrated pixels as mm."""
        from filament_inspector.ui.base import PaperMethodDiameterUI
        e = self.engines.get(self._selected_name)
        if e is None:
            messagebox.showwarning('No slices','Measure this view successfully first.')
            return
        needed = ('rectified_gray','valid','top_edges','bottom_edges',
                  'top_candidate_peaks_by_col','bottom_candidate_peaks_by_col',
                  'rejected_peaks_by_col','kept_peaks_by_col','intensity_crossing_by_col')
        for key in needed:
            setattr(self,key,getattr(e,key))
        measurement=self.view_results.get(self._selected_name,{})
        self.diameters_mm=e.diameters_mm if measurement.get('physical_stats') is not None else None
        self.gradient_multiplier.set(float(e.gradient_multiplier.get()))
        self.gradient_threshold.set(float(e.gradient_threshold.get()))
        self.use_signed_gradient.set(True)
        self.use_intensity_crossing.set(True)
        PaperMethodDiameterUI._show_slice_diagnostic(self)

    def _toggle_advanced(self):
        if self.v14_advanced_open:
            self.advanced_panel.grid_remove()
            self.adv_button.configure(text='▶ Advanced calibration & settings')
        else:
            self.advanced_panel.grid()
            self.adv_button.configure(text='▼ Advanced calibration & settings')
        self.v14_advanced_open=not self.v14_advanced_open

    def _save_layout(self):
        self._commit_fields()
        if self.image_cv_raw is None:
            messagebox.showwarning('No photo','Load an image first.')
            return
        if any(self.views[n]['search_roi_xyxy'] is None for n in VIEW_NAMES):
            messagebox.showwarning('Incomplete layout','Draw a Direct search ROI first.')
            return
        try:
            validate_shared_x({n:v['search_roi_xyxy'] for n,v in self.views.items()},self.image_cv.shape)
        except ValueError as exc:
            messagebox.showerror('Invalid layout',str(exc));return
        path=filedialog.asksaveasfilename(title='Save v14 geometry and view calibration',
            initialfile='three_view_layout_v14.json',defaultextension='.json',filetypes=[('JSON','*.json')])
        if not path:return
        fp=Path(path)
        views={}
        for n,v in self.views.items():
            item={k:val for k,val in v.items() if k!='previous_y'}
            if item['profile_path']:
                p=Path(item['profile_path'])
                if p.parent==fp.parent:item['profile_path']=p.name
            views[n]=item
        obj={'schema':'filament_three_view_v14_simple',
             'working_rotation_degrees_clockwise':90,
             'original_image_size_wh':[int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])],
             'shared_horizontal_search_interval':True,
             'vertical_only_mirror_adjustment':True,'views':views}
        fp.write_text(json.dumps(obj,indent=2),encoding='utf-8')
        self.status_var.set(f'Saved v14 layout: {fp.name}')

    def _load_layout(self):
        path=filedialog.askopenfilename(title='Load v14 layout',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            if self.image_cv_raw is None:
                raise ValueError('Load current photo first, so ROI coordinates can be checked.')
            obj=json.loads(Path(path).read_text(encoding='utf-8'))
            if obj.get('schema')!='filament_three_view_v14_simple':
                raise ValueError('Choose a v14 layout file, not the 2048x1152 v13 demo configuration.')
            if obj.get('working_rotation_degrees_clockwise')!=90:
                raise ValueError('Layout rotation must be 90 degrees clockwise')
            size=[int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])]
            if obj.get('original_image_size_wh')!=size:
                raise ValueError(f'Layout was saved for {obj.get("original_image_size_wh")}, current photo is {size}. Draw a new layout.')
            candidates={}
            for n in VIEW_NAMES:
                v=new_view(n)
                data=obj['views'][n]
                for k in v:
                    if k in data and k!='previous_y':v[k]=data[k]
                if v['profile'] is not None:
                    v['profile']=normalize_profile(v['profile'],n)
                if v['profile_path']:
                    source=Path(v['profile_path'])
                    if not source.is_absolute():v['profile_path']=str(Path(path).parent / source)
                candidates[n]=v
            validate_shared_x({n:v['search_roi_xyxy'] for n,v in candidates.items()},self.image_cv.shape)
            for n in VIEW_NAMES:
                if candidates[n]['profile'] is None:continue
                a=np.asarray(candidates[n]['profile']['local_planar_calibration']['homography_original_camera_px_to_mm'],float)
                for other in VIEW_NAMES:
                    if other==n or candidates[other]['profile'] is None:continue
                    b=np.asarray(candidates[other]['profile']['local_planar_calibration']['homography_original_camera_px_to_mm'],float)
                    if np.allclose(a/a[2,2],b/b[2,2],rtol=1e-9,atol=1e-9):
                        raise ValueError('View calibrations cannot share the same homography')
            self.views=candidates
            self._refresh_fields()
            self._refresh_v14_status()
            self.view_results.clear();self.engines.clear();self.last_detected.clear()
            self._reset_results_table();self._redraw()
            self.v14_hint.set('v14 layout loaded. Verify mirror positions and then click MEASURE ALL THREE VIEWS.')
        except Exception as exc:
            messagebox.showerror('Cannot load v14 layout',str(exc))


if __name__=='__main__':
    SimpleThreeViewUI().mainloop()

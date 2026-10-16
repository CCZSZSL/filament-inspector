#!/usr/bin/env python3
"""v14.6 centered fixed-width ROI GUI, inheriting the v14.5 monochrome pipeline.

Full-res GRAY8 snapshots, calibration, measurements, preview stream & RPi server
are UNCHANGED. 40-pixel X-band defaults to the experimentally aligned x=1350,
not the frame's mathematical center (1295.5). Users may override the fixed X.
"""
from __future__ import annotations

from filament_inspector.paths import DATA_ROOT
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from filament_inspector.ui.monochrome import IntegratedThreeViewUI
from filament_inspector.algorithms.roi import (
    centered_roi, centered_all_views, centered_description, fixed_x_band
)


class CenteredThreeViewUI(IntegratedThreeViewUI):
    FIXED_COLUMNS = 40
    DEFAULT_CENTER_X = 1350  # observed calibrated central rod location in user tests

    def _build_ui(self):
        self.fixed_center_x_var=tk.StringVar(value=str(self.DEFAULT_CENTER_X))
        super()._build_ui()
        self.title('Filament Inspector v14.6 | Fixed 40px ROI · Monochrome Live')
        base=self.adv_button.master
        for c in base.grid_slaves(row=0, column=0):
            if isinstance(c,ttk.Label):c.configure(text='Filament Inspector v14.6')
        for group in self.canvas.master.grid_slaves(row=0,column=0):
            for child in group.winfo_children():
                if isinstance(child,ttk.Label) and 'Three-view Inspection' in child.cget('text'):
                    child.configure(text='Three-view Inspection | v14.6 FIXED 40px MONO')

        zone=self.draw_button.master
        self.draw_button.configure(text='① Draw DIRECT height (fixed 40px X)', command=self._enable_draw)
        # Original controls are indexed rows 0..4. Add fixed-X controls between
        # Direct draw and Mirror regenerate; keep all inherited event bindings.
        for row in (4,3,2,1):
            for w in zone.grid_slaves(row=row,column=0):w.grid_configure(row=row+3)
        r=ttk.Frame(zone)
        r.grid(row=1,column=0,sticky='ew',pady=(0,4))
        ttk.Label(r,text='Locked ROI X center:').pack(side='left')
        ttk.Entry(r,textvariable=self.fixed_center_x_var,width=7).pack(side='left',padx=4)
        ttk.Button(r,text='Apply X',command=self._snap_existing_to_center).pack(side='left')
        ttk.Button(zone,text='Snap 3 ROIs to fixed X, width 40 px',
                   command=self._snap_existing_to_center).grid(row=2,column=0,sticky='ew',pady=(0,4))
        self.center_note=tk.StringVar()
        ttk.Label(zone,textvariable=self.center_note,wraplength=315,justify='left').grid(
            row=3,column=0,sticky='w',pady=(0,4))
        for w in zone.grid_slaves(row=5,column=0):
            if isinstance(w,ttk.Label):
                w.configure(text=('Draw vertical Y bounds across the DIRECT rod. '
                    f'Horizontal X and width are locked at the chosen center (default X={self.DEFAULT_CENTER_X}). '
                    'Mirror ROIs share the fixed X band; move mirrors vertically only.'))
        self.live_controls.capture_dir=DATA_ROOT/'captures_live_v146'
        self.v14_hint.set('Lock X center, draw Direct Y band. All 3 ROIs use 40 full-resolution columns. Use Capture + MEASURE for fresh frames.')
        self._show_center_note()
        self._bind_controls_wheel_recursive(zone)

    def _center_x(self):
        try:
            return float(self.fixed_center_x_var.get())
        except ValueError:
            raise ValueError('ROI X center must be a number (e.g. 1350)')

    def _show_center_note(self):
        if not hasattr(self,'center_note'):return
        shape=(4608,2592) if self.image_cv is None else self.image_cv.shape
        try:
            label=centered_description(shape,self.FIXED_COLUMNS,self._center_x())
        except ValueError as exc:
            label=f'Invalid ROI center: {exc}'
        self.center_note.set(label + ' | 90° CW working image')

    def _load_image(self,path):
        super()._load_image(path)
        self._show_center_note()

    def _enable_draw(self):
        super()._enable_draw()
        if self.v14_mode=='draw':
            self.v14_hint.set('Drag Direct vertical Y limits; X is locked to fixed 40-column band. Mouse horizontal position is ignored.')

    def _v14_press(self,event):
        if self.v14_mode!='draw':return super()._v14_press(event)
        if self.image_cv is None:return
        p=self._canvas_to_image(event.x,event.y)
        if p is None:return
        try:x0,x1=fixed_x_band(self.image_cv.shape,self.FIXED_COLUMNS,self._center_x())
        except ValueError as exc:
            messagebox.showwarning('ROI X center',str(exc));return
        self.v14_drag={'which':'direct','anchor':p}
        self.pending_box=((x0,p[1]),(x1,p[1]))
        self._draw_pending(*self.pending_box)

    def _v14_drag_motion(self,event):
        if not self.v14_drag or self.v14_drag.get('which')!='direct':
            return super()._v14_drag_motion(event)
        p=self._canvas_to_image(event.x,event.y)
        if p is None:return
        x0,x1=fixed_x_band(self.image_cv.shape,self.FIXED_COLUMNS,self._center_x())
        y0=self.v14_drag['anchor'][1]
        self.pending_box=((x0,min(y0,p[1])),(x1,max(y0,p[1])))
        self._draw_pending(*self.pending_box)

    def _v14_release(self,event):
        if not self.v14_drag or self.v14_drag.get('which')!='direct':
            return super()._v14_release(event)
        p=self._canvas_to_image(event.x,event.y)
        if p is not None:
            try:
                rect=centered_roi(self.image_cv.shape,
                    self.v14_drag['anchor'][1],p[1],self.FIXED_COLUMNS,
                    center_x=self._center_x())
                self._apply_direct_roi(rect)
            except ValueError as exc:
                messagebox.showwarning('Fixed-width Direct ROI',str(exc))
        self.pending_box=None
        self.v14_mode='pan'
        self.tool_mode.set('pan')
        self.v14_drag=None
        self._refresh_v14_status()
        self._redraw()

    def _apply_direct_roi(self,rect):
        if self.image_cv is None:return
        clean=centered_roi(self.image_cv.shape,rect[1],rect[3],
                           self.FIXED_COLUMNS,center_x=self._center_x())
        if any(self.views[k]['search_roi_xyxy'] is None for k in ('direct','left','right')):
            super()._apply_direct_roi(clean)
        else:
            # Maintain operator-adjusted mirror Y while snapping each shared X.
            old={k:self.views[k]['search_roi_xyxy'] for k in ('direct','left','right')}
            old['direct']=clean
            aligned=centered_all_views(self.image_cv.shape,old,self.FIXED_COLUMNS,
                                       center_x=self._center_x())
            for k,r in aligned.items():
                self.views[k]['search_roi_xyxy']=r
                self.views[k]['previous_y']=None
            self.view_results.clear();self.engines.clear();self.last_detected.clear()
            self._reset_results_table()
        self.v14_hint.set('X locked to 40 columns in all three views; mirror Y positions preserved when possible.')
        self._show_center_note()
        self._redraw()

    def _snap_existing_to_center(self):
        if self.image_cv is None:
            messagebox.showwarning('No frame','Capture full-resolution frame for ROI first.');return
        try:fixed_x_band(self.image_cv.shape,self.FIXED_COLUMNS,self._center_x())
        except ValueError as exc:
            messagebox.showwarning('Invalid X center',str(exc));return
        old=self.views['direct']['search_roi_xyxy']
        if old is None:
            self._show_center_note()
            self.v14_hint.set('X center set. Now draw Direct vertical Y limits.');return
        self._apply_direct_roi(old)

    def _load_layout(self):
        super()._load_layout()
        if self.image_cv is not None and self.views['direct']['search_roi_xyxy']:
            self._snap_existing_to_center()


if __name__=='__main__':
    CenteredThreeViewUI().mainloop()

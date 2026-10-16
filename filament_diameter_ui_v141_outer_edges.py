#!/usr/bin/env python3
"""v14.1: simple v14 GUI + background-guided outer-edge candidate gating.

Use the original v11 gradient, linear 50% crossing, homography and K_ref.
The only metrology change is to restrict which gradient anchors can be chosen.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import numpy as np

from filament_diameter_ui_v14_simple import SimpleThreeViewUI
from filament_three_view_core_v13 import VIEW_NAMES,VIEW_LABELS


class OuterEdgeThreeViewUI(SimpleThreeViewUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Diameter v14.1 | Outer-edge Guided Three-view Measurement')
        self.v141_show_edges = tk.BooleanVar(value=True)
        self.v141_outer_enable = tk.BooleanVar(value=True)
        self.v141_gate_radius = tk.DoubleVar(value=8.0)
        # Add ONE inspection toggle to the main workflow; no new setup screen.
        base = self.adv_button.master
        inspect = base.grid_slaves(row=5,column=0)[0]
        ttk.Checkbutton(inspect, text='Show measured top/bottom edges on image',
                        variable=self.v141_show_edges,command=self._redraw).grid(
                            row=3,column=0,sticky='w',pady=(6,0))
        ttk.Label(inspect, text='Top = green  /  Bottom = blue  /  invalid = red',
                  foreground='#555555').grid(row=4,column=0,sticky='w')
        adv=self.advanced_panel
        ttk.Separator(adv,orient='horizontal').grid(row=13,column=0,columnspan=2,sticky='ew',pady=6)
        ttk.Checkbutton(adv, text='Restrict peaks to outer silhouette',
                        variable=self.v141_outer_enable).grid(row=14,column=0,columnspan=2,sticky='w')
        ttk.Label(adv,text='Outer-edge gate radius (px)').grid(row=15,column=0,sticky='w')
        ttk.Spinbox(adv,from_=3,to=30,increment=1,width=10,
                    textvariable=self.v141_gate_radius).grid(row=15,column=1,sticky='e')
        ttk.Label(adv,text='The background estimates a rough outer silhouette. It is NOT used for 50% subpixel interpolation. A visually aligned edge is not proof of absolute accuracy.',
                  wraplength=316,justify='left').grid(row=16,column=0,columnspan=2,sticky='w',pady=(4,0))
        self._bind_controls_wheel_recursive(inspect)
        self._bind_controls_wheel_recursive(adv)
        self._refresh_fields()
        self.v14_hint.set('Draw Direct box → adjust green/blue boxes vertically → measure. Solid green/blue lines are the final detected edges.')

    def _commit_fields(self):
        super()._commit_fields()
        if not hasattr(self,'v141_outer_enable') or not hasattr(self,'views'):
            return
        view=self.views[self._selected_name]
        view['outer_edge_constraint']=bool(self.v141_outer_enable.get())
        try:
            radius=float(self.v141_gate_radius.get())
            if np.isfinite(radius) and 3<=radius<=30:
                view['outer_edge_gate_radius_px']=radius
        except (ValueError,tk.TclError):
            pass

    def _refresh_fields(self):
        super()._refresh_fields()
        if hasattr(self,'v141_outer_enable'):
            v=self.views[self._selected_name]
            self.v141_outer_enable.set(bool(v.get('outer_edge_constraint',True)))
            self.v141_gate_radius.set(float(v.get('outer_edge_gate_radius_px',8.0)))

    def _draw_canvas_overlays(self):
        super()._draw_canvas_overlays()
        if not hasattr(self,'v141_show_edges') or not self.v141_show_edges.get():
            return
        if self.image_cv is None or self.view_mode.get() != 'original':
            return
        c=self.canvas;s=self.display_scale;ox=self.display_offset_x;oy=self.display_offset_y
        if s<=0:
            return
        for view_key in VIEW_NAMES:
            engine=self.engines.get(view_key)
            result=self.view_results.get(view_key)
            if engine is None or result is None:
                continue
            x0,y0,x1,y1=result['roi']
            n=len(engine.valid)
            if x1-x0+1 != n:
                continue
            # Polylines in the ORIGINAL working-frame coordinates. Do not draw
            # the pink dynamic ROI as though it represented the real contour.
            for arr,color in ((engine.top_edges,'#00f06c'),(engine.bottom_edges,'#1b95ff')):
                segment=[]
                for idx in range(n):
                    if bool(engine.valid[idx]) and np.isfinite(arr[idx]):
                        px=ox+(x0+idx)*s;py=oy+(y0+float(arr[idx]))*s
                        if segment and px-segment[-2]>s*1.5:
                            if len(segment)>=4:
                                c.create_line(*segment,fill=color,width=2.5,tags=('v14_overlay','v141_edge'))
                            segment=[]
                        segment.extend((px,py))
                    else:
                        if len(segment)>=4:
                            c.create_line(*segment,fill=color,width=2.5,tags=('v14_overlay','v141_edge'))
                        segment=[]
                if len(segment)>=4:
                    c.create_line(*segment,fill=color,width=2.5,tags=('v14_overlay','v141_edge'))
            bad=np.flatnonzero(~np.asarray(engine.valid,dtype=bool))
            if len(bad):
                for i in bad[::max(1,int(np.ceil(len(bad)/60)))]:
                    x=ox+(x0+int(i))*s
                    mid=oy+(y0+y1)*0.5*s
                    c.create_line(x-3,mid-3,x+3,mid+3,fill='#ff3c38',width=2,tags=('v14_overlay','v141_edge'))
                    c.create_line(x-3,mid+3,x+3,mid-3,fill='#ff3c38',width=2,tags=('v14_overlay','v141_edge'))

    def _put_success(self,n,meas):
        super()._put_success(n,meas)
        values=list(self.result_table.item(n,'values'))
        if meas.get('outer_edge_constraint'):
            values[1] += ' · GUIDED'
            self.result_table.item(n,values=values)
            q=meas.get('outer_edge_validation',{})
            self.detail_var.set(self.detail_var.get()+f' | Outer-boundary agreement: median offset {q.get("median_abs_displacement_px",float("nan")):.2f}px (not accuracy)')
        else:
            values[1] += ' · UNCONSTRAINED'
            self.result_table.item(n,values=values)


if __name__=='__main__':
    OuterEdgeThreeViewUI().mainloop()

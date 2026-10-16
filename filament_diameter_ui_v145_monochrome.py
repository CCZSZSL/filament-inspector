#!/usr/bin/env python3
"""Filament Inspector v14.5: integrated live camera + full-resolution measurement.

The single original canvas shows Y-channel grayscale MJPEG video. All measurements continue
using unchanged v14.2.1 logic on a fresh 4608x2592 lossless grayscale frame.
"""
from __future__ import annotations
import math
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from filament_diameter_ui_v143_live import LiveThreeViewUI
from filament_live_integrated_v145 import IntegratedLiveControls


class IntegratedThreeViewUI(LiveThreeViewUI):
    def _build_ui(self):
        self._live_preview_pil = None
        self._live_native_wh = (4608,2592)
        self._live_active = False
        super()._build_ui()
        self.title('Filament Inspector v14.5 | Monochrome Live')
        # The inherited measurement canvas header is from v14.2; keep its code
        # but display the current integration version unambiguously.
        for group in self.canvas.master.grid_slaves(row=0,column=0):
            for widget in group.winfo_children():
                if isinstance(widget,ttk.Label) and 'Three-view Inspection' in widget.cget('text'):
                    widget.configure(text='Three-view Inspection | v14.5 MONO')
        # The original left panel is scrollable (including a visible scrollbar).
        base = self.adv_button.master
        old_images = base.grid_slaves(row=2, column=0)[0]
        old_images.grid_remove()
        # Hide the v14.3 pop-up action; camera controls live in this panel instead.
        for c in old_images.winfo_children():
            if isinstance(c,ttk.Button) and 'LIVE VIDEO' in c.cget('text'):
                c.grid_remove()
        for row in reversed(range(3,9)):
            for widget in base.grid_slaves(row=row,column=0):
                widget.grid_configure(row=row+1)
        self.live_controls = IntegratedLiveControls(self,base)
        self.live_controls.grid(row=2,column=0,sticky='ew',pady=(0,8))
        # Previous inheritance chain sets outdated v14.3.1 heading; update it.
        for c in base.grid_slaves(row=0,column=0):
            if isinstance(c,ttk.Label):
                c.configure(text='Filament Inspector v14.5')
        self.live_panel = self.live_controls # compatibility reference
        self._bind_controls_wheel_recursive(self.live_controls)
        self.after(450, self.live_controls._connect)  # start video in the main canvas automatically
        self.v14_hint.set('MONO Y preview only. Background + ROI -> Continuous Measure uses lossless full-resolution GRAY8 frames.')

    def _on_live_preview(self,frame, native_wh):
        self._live_preview_pil = frame
        self._live_native_wh = tuple(native_wh)
        if self._live_active:
            self._redraw()

    def _redraw(self):
        if (getattr(self,'_live_active',False) and
                getattr(self,'_live_preview_pil',None) is not None and
                self.view_mode.get()=='original'):
            self._redraw_video()
        else:
            super()._redraw()

    def _redraw_video(self):
        """Draw preview rescaled into native (90°CW) canvas coordinates.

        Search ROIs stored in full-res native coordinates remain correct.
        Only the viewport crop is resized; zooming does not allocate a giant PIL.
        """
        if not hasattr(self,'canvas'):return
        preview=self._live_preview_pil
        if preview is None:return
        orig_w,orig_h=self._live_native_wh
        iw,ih=orig_h,orig_w  # after 90° CW rotation
        cw=max(self.canvas.winfo_width(),300)
        ch=max(self.canvas.winfo_height(),250)
        scale=max(1e-9,min(cw/iw,ch/ih)*self.zoom_factor)
        ox=(cw-iw*scale)/2+self.pan_x
        oy=(ch-ih*scale)/2+self.pan_y
        self.display_scale,self.display_offset_x,self.display_offset_y=scale,ox,oy
        self.canvas.delete('all')
        x0=max(0,(0-ox)/scale);y0=max(0,(0-oy)/scale)
        x1=min(iw,(cw-ox)/scale);y1=min(ih,(ch-oy)/scale)
        if x1>x0 and y1>y0:
            # Fractional coordinates in preview, using the SAME total sensor FOV.
            px0=max(0,math.floor(x0*preview.width/iw))
            py0=max(0,math.floor(y0*preview.height/ih))
            px1=min(preview.width,math.ceil(x1*preview.width/iw))
            py1=min(preview.height,math.ceil(y1*preview.height/ih))
            if px1>px0 and py1>py0:
                crop=preview.crop((px0,py0,px1,py1))
                left=ox+px0*iw/preview.width*scale
                top=oy+py0*ih/preview.height*scale
                out_w=max(1,int(round((px1-px0)*iw/preview.width*scale)))
                out_h=max(1,int(round((py1-py0)*ih/preview.height*scale)))
                crop=crop.resize((out_w,out_h),Image.Resampling.BILINEAR)
                self.preview_photo=ImageTk.PhotoImage(crop)
                self.canvas.create_image(left,top,anchor='nw',image=self.preview_photo)
        self.zoom_label_var.set(f'Zoom: {self.zoom_factor*100:.0f}% | LIVE MONO (full-res gray8 measurement)')
        self._update_cursor()
        if hasattr(self,'views'):
            self._draw_canvas_overlays()

    def destroy(self):
        if hasattr(self,'live_controls'):
            self.live_controls.close()
        super().destroy()


if __name__ == '__main__':
    IntegratedThreeViewUI().mainloop()

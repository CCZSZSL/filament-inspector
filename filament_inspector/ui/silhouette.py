#!/usr/bin/env python3
"""v14.2 simplified three-view inspection.

Direct: original v11 Signed Gradient + 50% crossing + its H/K_ref.
Mirrors: default background-assisted SILHOUETTE + 50% crossing on the original
current image. Mirrors may be locally scaled by an INDEPENDENT 2.000 mm rod
without pretending a single physical width is a planar homography.
"""
from __future__ import annotations
import csv
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np

from filament_inspector.ui.edge_constraints import OuterEdgeThreeViewUI
from filament_inspector.ui.three_view import ThreeViewUI
from filament_inspector.algorithms.views import VIEW_NAMES, VIEW_LABELS
from filament_inspector.algorithms.silhouette import process_silhouette_view


class SilhouetteThreeViewUI(OuterEdgeThreeViewUI):
    def _build_ui(self):
        super()._build_ui()
        self.title('Filament Diameter v14.2.1 | Silhouette Boundary Guard Fix')
        base=self.adv_button.master
        for child in base.grid_slaves(row=0,column=0):
            if isinstance(child,ttk.Label):child.configure(text='Filament Inspector  v14.2.1')
        main=self.canvas.master
        for header in main.grid_slaves(row=0,column=0):
            for child in header.winfo_children():
                if isinstance(child,ttk.Label) and 'Three-view Inspection' in str(child.cget('text')):
                    child.configure(text='Three-view Inspection | v14.2.1')
        self.v142_method_var=tk.StringVar(value='gradient')
        self.v142_scale_note=tk.StringVar(value='Mirror scale: not calibrated')
        self.v142_show_mask=tk.BooleanVar(value=False)
        # Put one rod calibration action where an operator can find it, while
        # preserving all four original workflow sections.
        inspect=self.adv_button.master.grid_slaves(row=5,column=0)[0]
        self.v142_rod_button=ttk.Button(inspect,text='Calibrate MIRROR scale (2.000 mm rod)',
                                       command=self._calibrate_mirror_scale)
        self.v142_rod_button.grid(row=5,column=0,sticky='ew',pady=(6,0))
        ttk.Label(inspect,textvariable=self.v142_scale_note,wraplength=315).grid(
            row=6,column=0,sticky='w',pady=(3,0))
        ttk.Button(inspect,text='Preview selected silhouette mask',
                   command=self._show_silhouette_mask).grid(row=7,column=0,sticky='ew',pady=(4,0))
        adv=self.advanced_panel
        ttk.Separator(adv,orient='horizontal').grid(row=17,column=0,columnspan=2,sticky='ew',pady=(8,5))
        ttk.Label(adv,text='Edge detection method').grid(row=18,column=0,sticky='w')
        method=ttk.Combobox(adv,textvariable=self.v142_method_var,
                           values=('gradient','silhouette'),state='readonly',width=14)
        method.grid(row=18,column=1,sticky='e')
        method.bind('<<ComboboxSelected>>',lambda _ : self._commit_fields())
        ttk.Button(adv,text='Save selected mirror scale JSON',command=self._export_mirror_scale).grid(
            row=19,column=0,columnspan=2,sticky='ew',pady=(5,0))
        ttk.Button(adv,text='Load selected mirror scale JSON',command=self._import_mirror_scale).grid(
            row=20,column=0,columnspan=2,sticky='ew',pady=(3,0))
        ttk.Button(adv,text='Clear selected mirror scale',command=self._clear_mirror_scale).grid(
            row=21,column=0,columnspan=2,sticky='ew',pady=(3,0))
        ttk.Label(adv,text=('A mirror local scale is valid only near the same optical '
                   'position at the same resolution. Verify using an independent rod. '
                   'It is NOT a Homography or general accuracy certification.'),
                  wraplength=312).grid(row=22,column=0,columnspan=2,sticky='w',pady=(5,0))
        # Hide potentially misleading legacy K_ref button on the visible
        # advanced panel; Direct's K_ref can still be checked in its profile.
        for widget in adv.grid_slaves(row=8,column=0):
            if isinstance(widget,ttk.Button):
                widget.configure(text='Calibrate DIRECT K_ref (requires planar H)')
        self._bind_controls_wheel_recursive(inspect)
        self._bind_controls_wheel_recursive(adv)
        self._refresh_fields()
        self.v14_hint.set('Draw Direct → drag mirror boxes vertically → Measure. Mirrors default to SILHOUETTE. Use 2.000 mm rod to calibrate each mirror independently.')

    def _commit_fields(self):
        super()._commit_fields()
        if hasattr(self,'v142_method_var') and hasattr(self,'views'):
            method=self.v142_method_var.get()
            if method in ('gradient','silhouette'):
                self.views[self._selected_name]['detection_method']=method

    def _refresh_fields(self):
        super()._refresh_fields()
        if not hasattr(self,'v142_method_var'):return
        v=self.views[self._selected_name]
        self.v142_method_var.set(v.get('detection_method','silhouette' if self._selected_name!='direct' else 'gradient'))
        scale=v.get('local_scale_calibration')
        if self._selected_name=='direct':
            self.v142_scale_note.set('Direct uses its existing Homography × K_ref (not mirror scale).')
            self.v142_rod_button.configure(state='disabled')
        elif scale:
            self.v142_scale_note.set(f"{VIEW_LABELS[self._selected_name]}: {float(scale['mm_per_px']):.8f} mm/px from {scale['rod_width_mm']:.3f} mm rod; verify separately")
            self.v142_rod_button.configure(state='normal')
        else:
            self.v142_scale_note.set(f'{VIEW_LABELS[self._selected_name]}: no local mirror scale (pixel only)')
            self.v142_rod_button.configure(state='normal')
        self._refresh_v14_status()

    def _refresh_v14_status(self):
        super()._refresh_v14_status()
        if not hasattr(self,'views'):return
        if self._selected_name!='direct':
            cal=self.views[self._selected_name].get('local_scale_calibration')
            if cal:
                same = self.image_cv_raw is None or cal.get('original_image_size_wh') == [self.image_cv_raw.shape[1],self.image_cv_raw.shape[0]]
                self.v14_cal_label.set(f"{VIEW_LABELS[self._selected_name]}: {'LOCAL SCALE (verify)' if same else 'SCALE RESOLUTION MISMATCH'}")

    def _process_one(self,n):
        if self.views[n].get('detection_method')!='silhouette':
            return super()._process_one(n)
        try:
            self._require_photos()
            v=self.views[n]
            if v['search_roi_xyxy'] is None:raise ValueError('Draw/align Search ROI first')
            found,m,e=process_silhouette_view(self.image_cv,self.background_cv,self.image_cv_raw.shape,v)
            self.last_detected[n]=found
            v['previous_y']=found['center_y']
            self.view_results[n]=m
            self.engines[n]=e
            self._put_success(n,m)
        except Exception as exc:
            self.last_detected.pop(n,None)
            self._put_failure(n,'SILHOUETTE FAILED',str(exc))

    def _put_success(self,n,meas):
        if meas.get('detection_method')=='silhouette':
            # Bypass v14.1's gradient-only outer guide annotation.
            ThreeViewUI._put_success(self,n,meas)
            vals=list(self.result_table.item(n,'values'))
            vals[1] = ('LOCAL SCALE · VERIFY' if meas['physical_stats'] is not None else 'PIXEL ONLY')+' · SILHOUETTE'
            self.result_table.item(n,values=vals)
            return
        return super()._put_success(n,meas)

    def _calibrate_mirror_scale(self):
        n=self._selected_name
        if n=='direct':
            messagebox.showinfo('Direct calibration','The Direct View uses H × K_ref, not a mirror pixel scale.')
            return
        if self.views[n].get('detection_method')!='silhouette':
            messagebox.showwarning('Use silhouette','Choose silhouette detection method for this mirror first.')
            return
        if self.image_cv_raw is None:
            messagebox.showwarning('Missing photo','Load a photo of your 2.000 mm precision reference rod first.')
            return
        try:known=float(self.known_width_var.get())
        except (ValueError,tk.TclError):known=0
        if not 0<known<20:
            messagebox.showerror('Invalid reference','Enter the exact known rod diameter (default 2.000 mm).');return
        if not messagebox.askyesno('Confirm physical reference',
            f"Is the CURRENT photo a real {known:.3f} mm precision rod, with BOTH measured outer edges verified visually for {VIEW_LABELS[n]}?\n\n"
            'This will create a LOCAL mm/px scale for this mirror only. The same rod is NOT an independent accuracy check.'):
            return
        # Fresh silhouette-only measurement, ignoring any prior scale.
        v=dict(self.views[n],local_scale_calibration=None)
        try:
            found,meas,engine=process_silhouette_view(self.image_cv,self.background_cv,self.image_cv_raw.shape,v)
            valid=meas['valid_slices'];total=meas['total_slices']
            if valid/max(1,total)<0.85:
                raise ValueError(f'Only {valid}/{total} valid slices; >=85% required for reference calibration')
            pixels=float(meas['pixel_stats']['median'])
            if pixels<=0 or not np.isfinite(pixels):raise ValueError('Invalid measured rod width')
            scale=known/pixels
            search=self.views[n]['search_roi_xyxy']
            cal={'schema':'mirror_local_scale_v142','view_name':VIEW_LABELS[n],
                 'working_rotation_degrees_clockwise':90,
                 'original_image_size_wh':[int(self.image_cv_raw.shape[1]),int(self.image_cv_raw.shape[0])],
                 'rod_width_mm':known,'rod_median_width_px':pixels,
                 'rod_pixel_std':float(meas['pixel_stats']['std']),
                 'rod_valid_slices':valid,'rod_total_slices':total,
                 'mm_per_px':scale,'search_roi_xyxy_at_calibration':list(search),
                 'search_roi_center_xy':[(search[0]+search[2])/2,(search[1]+search[3])/2],
                 'method':'silhouette_mask_plus_original_intensity_half_crossing',
                 'reference_image':str(self.image_path),
                 'note':'Same-rod remeasurement is not independent validation. Fixed local geometry only.'}
            self.views[n]['local_scale_calibration']=cal
            self.last_detected[n]=found;self.engines[n]=engine
            self.view_results.pop(n,None)
            self._refresh_fields()
            self._process_one(n)
            self._redraw()
            messagebox.showinfo('Mirror scale stored',
                f"{VIEW_LABELS[n]}\nKnown rod: {known:.3f} mm\nMedian width: {pixels:.3f} px\n"
                f"Local scale: {scale:.8f} mm/px\n\nSave ROI layout or export scale JSON. Check with a DIFFERENT reference diameter.")
        except Exception as exc:
            messagebox.showerror('Mirror calibration failed',str(exc))

    def _export_mirror_scale(self):
        n=self._selected_name;data=self.views[n].get('local_scale_calibration')
        if n=='direct' or data is None:
            messagebox.showwarning('No mirror scale','Calibrate selected mirror with a 2.000 mm rod first.');return
        p=filedialog.asksaveasfilename(initialfile=f'{n}_mirror_local_scale_v142.json',
              defaultextension='.json',filetypes=[('JSON','*.json')])
        if p:Path(p).write_text(json.dumps(data,indent=2),encoding='utf-8')

    def _import_mirror_scale(self):
        n=self._selected_name
        if n=='direct':messagebox.showwarning('Mirror only','Select Left or Right Mirror first.');return
        p=filedialog.askopenfilename(filetypes=[('JSON','*.json')])
        if not p:return
        try:
            c=json.loads(Path(p).read_text(encoding='utf-8'))
            if c.get('schema')!='mirror_local_scale_v142' or c.get('view_name')!=VIEW_LABELS[n]:
                raise ValueError('Wrong mirror or calibration schema')
            if c.get('working_rotation_degrees_clockwise')!=90:raise ValueError('Rotation mismatch')
            if self.image_cv_raw is not None and c.get('original_image_size_wh') != [self.image_cv_raw.shape[1],self.image_cv_raw.shape[0]]:
                raise ValueError('Calibration image size mismatch')
            if not np.isfinite(float(c['mm_per_px'])) or float(c['mm_per_px'])<=0:raise ValueError('Invalid mm/px')
            self.views[n]['local_scale_calibration']=c
            self._refresh_fields();self._reset_results_table();self.view_results.clear();self.engines.clear()
            self.v14_hint.set(f'{VIEW_LABELS[n]} mirror scale loaded; measure and independently validate.')
        except Exception as exc:messagebox.showerror('Scale rejected',str(exc))

    def _clear_mirror_scale(self):
        n=self._selected_name
        if n=='direct':return
        self.views[n]['local_scale_calibration']=None
        self._refresh_fields();self._reset_results_table();self.view_results.clear();self.engines.clear()

    def _export_profile(self):
        if self._selected_name!='direct' and self.views[self._selected_name].get('detection_method')=='silhouette':
            return self._export_mirror_scale()
        return super()._export_profile()

    def _export_results(self):
        if not self.view_results:messagebox.showwarning('No results','Measure a view first.');return
        p=filedialog.asksaveasfilename(initialfile='three_view_results_v142.json',
             defaultextension='.json',filetypes=[('JSON','*.json')])
        if not p:return
        root={'schema':'filament_three_view_results_v142','image':str(self.image_path),
              'background':str(self.background_path),'roundness':'NOT computed',
              'note':'mm via Direct H×K_ref or each mirror local mm/px. Heuristic confidence is NOT accuracy.',
              'views':{}}
        for n,m in self.view_results.items():
            d=self.last_detected[n]
            loc={k:v for k,v in d.items() if not isinstance(v,np.ndarray)}
            root['views'][n]={'measurement':m,'localization':loc,
                'mirror_local_scale':self.views[n].get('local_scale_calibration')}
        Path(p).write_text(json.dumps(root,indent=2,default=lambda o: o.item() if isinstance(o,np.generic) else str(o)),encoding='utf-8')

    def _export_csv(self):
        if not self.engines:messagebox.showwarning('No results','Measure first.');return
        p=filedialog.asksaveasfilename(initialfile='three_view_slices_v142.csv',
                defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not p:return
        with open(p,'w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(['view','detection_method','slice_x','valid','top_y_roi_px','bottom_y_roi_px',
                                        'diameter_px','diameter_mm_if_calibrated','scale_type'])
            for n,e in self.engines.items():
                m=self.view_results[n];method=m.get('detection_method','gradient')
                for x,ok in enumerate(e.valid):
                    diameter=float(e.widths_px[x]) if ok else None
                    if ok and m['physical_stats'] is not None:
                        millimeters=(diameter*m['scale_mm_per_px'] if method=='silhouette'
                                     else float(e.diameters_mm[x]))
                    else:millimeters=None
                    w.writerow([VIEW_LABELS[n],method,x,bool(ok),float(e.top_edges[x]) if ok else None,
                                float(e.bottom_edges[x]) if ok else None,diameter,millimeters,
                                'mirror_local' if method=='silhouette' else 'H_x_Kref'])

    def _show_silhouette_mask(self):
        from PIL import Image,ImageTk,ImageDraw
        name=self._selected_name
        m=self.view_results.get(name)
        e=self.engines.get(name)
        d=self.last_detected.get(name)
        if m is None or m.get('detection_method')!='silhouette' or e is None or d is None:
            messagebox.showinfo('Mask unavailable','Measure the selected view using silhouette detection first.');return
        mask=d['mask_for_diagnostics']
        diff=d['difference_for_diagnostics']
        # Left: real segmentation mask, Right: colour/brightness change magnitude.
        left=np.stack([mask*255]*3,axis=2).astype(np.uint8)
        right=np.stack([np.clip(diff*2.5,0,255).astype(np.uint8)]*3,axis=2)
        both=np.concatenate([left,right],axis=1)
        pil=Image.fromarray(both)
        draw=ImageDraw.Draw(pil)
        w=mask.shape[1]
        search_y=d['search_xyxy'][1]
        roi=m['roi']
        for idx in np.flatnonzero(e.valid):
            x=roi[0]-d['search_xyxy'][0]+int(idx)
            for ylocal,color in ((e.top_edges[idx],'#00ff66'),(e.bottom_edges[idx],'#2baaff')):
                y=roi[1]+float(ylocal)-search_y
                if 0<=x<w and 0<=y<mask.shape[0]:draw.point((x,y),fill=color)
        zoom=max(1,min(4,int(820/max(1,pil.width))))
        zoomed=pil.resize((pil.width*zoom,pil.height*zoom),Image.Resampling.NEAREST)
        win=tk.Toplevel(self);win.title(f'Silhouette mask vs background difference — {VIEW_LABELS[name]}')
        photo=ImageTk.PhotoImage(zoomed)
        label=ttk.Label(win,image=photo);label.image=photo;label.pack(padx=10,pady=10)
        ttk.Label(win,text='LEFT: binary foreground mask + actual subpixel edge points (green/blue). RIGHT: colour-aware difference ×2.5. Internal bright stripes must not become outer boundaries.',wraplength=850).pack(padx=12,pady=(0,12))

    def _selected_diagnostic(self):
        m=self.view_results.get(self._selected_name)
        if not m or m.get('detection_method')!='silhouette':
            return super()._selected_diagnostic()
        e=self.engines[self._selected_name]
        cols=np.flatnonzero(e.valid)
        if not len(cols):messagebox.showwarning('No slices','No valid slices');return
        x=int(cols[np.argmin(abs(cols-len(e.valid)/2))])
        f=e.rectified_gray[:,x].astype(float)
        win=tk.Toplevel(self);win.title(f'Silhouette Slice Diagnostic — {VIEW_LABELS[self._selected_name]}');win.geometry('920x540')
        ttk.Label(win,text='Original grayscale intensity (blue), measured 50% crossings (green/cyan); pixel coordinates increase downwards.',
                  wraplength=850).pack(anchor='w',padx=12,pady=8)
        can=tk.Canvas(win,bg='white');can.pack(fill='both',expand=True,padx=12,pady=10)
        def draw(_=None):
            can.delete('all');W=max(500,can.winfo_width());H=max(300,can.winfo_height())
            l,t=55,30;cw=W-85;ch=H-85
            X=lambda i:l+float(i)/(len(f)-1)*cw
            Y=lambda a:t+ch-(float(a)/255.)*ch
            can.create_line(l,t,l,t+ch,l+cw,t+ch,fill='black')
            coords=[]
            for i,p in enumerate(f):coords.extend((X(i),Y(p)))
            can.create_line(*coords,fill='#2563eb',width=2)
            for value,color,label in ((float(e.top_edges[x]),'#17a34a','TOP'),(float(e.bottom_edges[x]),'#0ea5e9','BOTTOM')):
                can.create_line(X(value),t,X(value),t+ch,fill=color,width=2,dash=(4,3))
                can.create_text(X(value)+4,t+15,text=f'{label} {value:.3f}px',anchor='w',fill=color)
            can.create_text(l,t+ch+25,anchor='w',text=f'Column {x}; physical width = {e.widths_px[x]:.3f}px | method: silhouette mask + local 50% intensity crossing')
        can.bind('<Configure>',draw);win.after(80,draw)


if __name__=='__main__':
    SilhouetteThreeViewUI().mainloop()

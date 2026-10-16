#!/usr/bin/env python3
"""Filament Diameter Measurement v13 | Three independent views.

Mac host GUI. Requires unmodified v11.py and v12.py alongside this program.
No mirror millimetres are reported without a matching independent planar
homography AND reference-rod K_ref.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from PIL import ImageDraw

from filament_inspector.ui.base import PaperMethodDiameterUI
from filament_inspector.ui.background import BackgroundTrackingUI
from filament_inspector.algorithms.views import (
    VIEW_NAMES, VIEW_LABELS, new_view, normalize_profile, calibration_status,
    process_view, locate_view, heuristic_confidence,
)

PALETTE = {"direct":"#ed8d17", "left":"#30bb80", "right":"#35a5e8"}


class ThreeViewUI(BackgroundTrackingUI):
    def _build_ui(self):
        super()._build_ui()
        self.title("Filament Diameter v13 | Three-view Automatic Measurement")
        self.views = {n: new_view(n) for n in VIEW_NAMES}
        self.view_results = {}
        self.last_detected = {}
        self.engines = {}
        self._selected_name = "direct"
        self.selected_name_var = tk.StringVar(value=VIEW_LABELS["direct"])
        self.polarity_var = tk.StringVar(value="bright")
        self.approx_px_var = tk.DoubleVar(value=75.0)
        self.foreground_var = tk.DoubleVar(value=14.0)
        self.padding_var = tk.IntVar(value=14)
        self.known_width_var = tk.DoubleVar(value=2.0)
        self.profile_status_var = tk.StringVar(value="Direct: no profile")
        self.search_status_var = tk.StringVar(value="Set a search area for each view")

        pane = self.controls_frame
        multi = ttk.LabelFrame(pane,text="8. v13 THREE-VIEW AUTO MEASUREMENT",padding=10)
        multi.grid(row=8,column=0,sticky="ew",pady=(10,10))
        multi.columnconfigure(0,weight=1)
        multi.columnconfigure(1,weight=1)
        ttk.Label(multi,text="View for editing / single-view debugging").grid(row=0,column=0,columnspan=2,sticky="w")
        choose=ttk.Combobox(multi,textvariable=self.selected_name_var,
                            values=[VIEW_LABELS[k] for k in VIEW_NAMES],state="readonly",width=28)
        choose.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(2,7))
        choose.bind("<<ComboboxSelected>>",self._selected_changed)
        ttk.Label(multi,text="Foreground polarity").grid(row=2,column=0,sticky="w")
        pol=ttk.Combobox(multi,textvariable=self.polarity_var,
                         values=["bright","dark"],state="readonly",width=10)
        pol.grid(row=2,column=1,sticky="e")
        pol.bind("<<ComboboxSelected>>",lambda e:self._commit_fields())
        ttk.Label(multi,text="Expected width (pixels, coarse only)").grid(row=3,column=0,sticky="w")
        ttk.Spinbox(multi,from_=5,to=200,increment=1,width=9,textvariable=self.approx_px_var).grid(row=3,column=1,sticky="e")
        ttk.Label(multi,text="Difference threshold").grid(row=4,column=0,sticky="w")
        ttk.Spinbox(multi,from_=5,to=120,increment=2,width=9,textvariable=self.foreground_var).grid(row=4,column=1,sticky="e")
        ttk.Label(multi,text="Background padding (px)").grid(row=5,column=0,sticky="w")
        ttk.Spinbox(multi,from_=10,to=50,increment=2,width=9,textvariable=self.padding_var).grid(row=5,column=1,sticky="e")
        ttk.Button(multi,text="Set selected SEARCH from 4 image points",
                   command=self._set_search_from_points).grid(row=6,column=0,columnspan=2,sticky="ew",pady=(8,3))
        ttk.Button(multi,text="Load calibration for SELECTED view...",
                   command=self._load_view_profile).grid(row=7,column=0,columnspan=2,sticky="ew",pady=2)
        ttk.Label(multi,textvariable=self.profile_status_var,wraplength=335).grid(row=8,column=0,columnspan=2,sticky="w",pady=3)
        ttk.Label(multi,textvariable=self.search_status_var,wraplength=335).grid(row=9,column=0,columnspan=2,sticky="w",pady=3)
        ttk.Label(multi,text="Known rod width (mm)").grid(row=10,column=0,sticky="w",pady=(4,0))
        ttk.Spinbox(multi,from_=0.1,to=10.0,increment=.001,width=9,
                    textvariable=self.known_width_var).grid(row=10,column=1,sticky="e")
        ttk.Button(multi,text="CALIBRATE K_ref for selected view (rod photo)",
                   command=self._calibrate_rod).grid(row=11,column=0,columnspan=2,sticky="ew",pady=3)
        ttk.Button(multi,text="Export selected calibration profile...",
                   command=self._export_profile).grid(row=12,column=0,columnspan=2,sticky="ew",pady=3)
        ttk.Separator(multi,orient="horizontal").grid(row=13,column=0,columnspan=2,sticky="ew",pady=8)
        ttk.Button(multi,text="Locate + Measure SELECTED view",
                   command=self.process_selected).grid(row=14,column=0,columnspan=2,sticky="ew",pady=2)
        ttk.Button(multi,text="PROCESS ALL THREE VIEWS",command=self.process_all).grid(
            row=15,column=0,columnspan=2,sticky="ew",pady=4,ipady=5)
        ttk.Button(multi,text="Selected Slice Diagnostic",
                   command=self._selected_diagnostic).grid(row=16,column=0,columnspan=2,sticky="ew",pady=2)
        ttk.Button(multi,text="Save v13 Configuration...",
                   command=self._save_config).grid(row=17,column=0,sticky="ew",padx=(0,2),pady=3)
        ttk.Button(multi,text="Load v13 Configuration...",
                   command=self._load_config).grid(row=17,column=1,sticky="ew",padx=(2,0),pady=3)
        ttk.Button(multi,text="Export three-view results JSON...",
                   command=self._export_results).grid(row=18,column=0,columnspan=2,sticky="ew",pady=3)
        ttk.Button(multi,text="Export per-slice CSV...",
                   command=self._export_csv).grid(row=19,column=0,columnspan=2,sticky="ew",pady=3)
        ttk.Label(multi,wraplength=330,text=(
            "Use the original image for all sub-pixel edges. The background is used ONLY "
            "for locating. Missing mirror calibration means PIXEL ONLY, not mm. "
            "Confidence is a detection QUALITY HEURISTIC (not an accuracy probability)."
        )).grid(row=20,column=0,columnspan=2,sticky="w",pady=(7,0))

        # Insert a compact result table below the existing image canvas.
        main=self.canvas.master
        for child in main.winfo_children():
            if isinstance(child,ttk.Label) and str(child.cget("textvariable"))==str(self.status_var):
                child.grid_configure(row=5)
        frame=ttk.LabelFrame(main,text="Three-view results — each view independently calibrated",padding=5)
        frame.grid(row=3,column=0,sticky="ew",pady=(6,0))
        frame.columnconfigure(0,weight=1)
        columns=("view","state","roi","valid","median","mean","std","confidence")
        self.result_table=ttk.Treeview(frame,columns=columns,show="headings",height=3,selectmode="browse")
        labels=["View","Status","Dynamic ROI","Valid slices","Median","Mean","Std","Confidence*"]
        widths=[100,140,165,100,95,95,90,100]
        for field,label,width in zip(columns,labels,widths):
            self.result_table.heading(field,text=label)
            self.result_table.column(field,width=width,anchor="center",stretch=field in ("state","roi"))
        self.result_table.grid(row=0,column=0,sticky="ew")
        self.result_table.bind("<<TreeviewSelect>>",self._table_selected)
        for name in VIEW_NAMES:
            self.result_table.insert("", "end", iid=name,values=[VIEW_LABELS[name],"NOT RUN","—","—","—","—","—","—"])
        self.detail_var=tk.StringVar(value="* Confidence is a non-probabilistic heuristic. Physical mm appears only with view-specific H and K_ref.")
        ttk.Label(main,textvariable=self.detail_var,wraplength=1050).grid(row=4,column=0,sticky="w",pady=(2,0))
        self._refresh_fields()
        self._bind_controls_wheel_recursive(multi)

    def _selected_changed(self,event=None):
        self._commit_fields()
        label=self.selected_name_var.get()
        self._selected_name=next((k for k,v in VIEW_LABELS.items() if v==label),"direct")
        self._refresh_fields()
        self._redraw()

    def _table_selected(self,event=None):
        selected=self.result_table.selection()
        if selected:
            self._commit_fields()
            self._selected_name=selected[0]
            self.selected_name_var.set(VIEW_LABELS[self._selected_name])
            self._refresh_fields()
            self._redraw()

    def _commit_fields(self):
        if not hasattr(self,"views"):return
        v=self.views[self._selected_name]
        v["polarity"]=self.polarity_var.get()
        try:
            px=float(self.approx_px_var.get());threshold=float(self.foreground_var.get());pad=int(self.padding_var.get())
            if px>0 and threshold>0 and pad>=3:
                v["approx_expected_width_px"]=px
                v["foreground_threshold"]=threshold
                v["edge_padding_px"]=pad
        except (tk.TclError,ValueError):pass

    def _refresh_fields(self):
        v=self.views[self._selected_name]
        self.polarity_var.set(v["polarity"])
        self.approx_px_var.set(v["approx_expected_width_px"])
        self.foreground_var.set(v["foreground_threshold"])
        self.padding_var.set(v["edge_padding_px"])
        self.search_status_var.set(f"SEARCH: {v['search_roi_xyxy'] or 'NOT SET'} (working 90° CW pixels)")
        self.profile_status_var.set(f"{VIEW_LABELS[self._selected_name]}: {calibration_status(v['profile'])}"
                                    + (f" | {Path(v['profile_path']).name}" if v['profile_path'] else ""))

    def _set_search_from_points(self):
        self._commit_fields()
        if self.image_cv is None or len(self.roi_points)!=4:
            messagebox.showwarning("SEARCH ROI", "Load an image and select four ROI corners (TL,TR,BR,BL) first.")
            return
        box=list(self._roi_bounds())
        self.views[self._selected_name]["search_roi_xyxy"]=box
        # The same clicked ROI can now be reused for the next view after Reset 4 Points.
        self._refresh_fields()
        self._redraw()

    def _load_view_profile(self):
        p=filedialog.askopenfilename(title=f"Independent calibration for {VIEW_LABELS[self._selected_name]}",
                                     filetypes=[("JSON","*.json")])
        if not p:return
        try:
            obj=json.loads(Path(p).read_text(encoding="utf-8"))
            profile=normalize_profile(obj,self._selected_name)
            self._assert_independent(profile,self._selected_name)
            self.views[self._selected_name]["profile"]=profile
            self.views[self._selected_name]["profile_path"]=str(Path(p).resolve())
            if self._selected_name=="direct":
                zone=(profile.get("dynamic_tracking") or {}).get("search_roi_xyxy")
                if zone and self.views["direct"]["search_roi_xyxy"] is None:
                    self.views["direct"]["search_roi_xyxy"]=zone
            self._refresh_fields()
            self.status_var.set(f"Loaded {VIEW_LABELS[self._selected_name]} profile: {Path(p).name}")
        except Exception as exc:
            messagebox.showerror("Calibration load blocked",str(exc))

    def _assert_independent(self,profile,key):
        H=np.asarray(profile["local_planar_calibration"]["homography_original_camera_px_to_mm"],float)
        for other_key,other in self.views.items():
            if other_key==key or other["profile"] is None:continue
            H2=np.asarray(other["profile"]["local_planar_calibration"]["homography_original_camera_px_to_mm"],float)
            if np.allclose(H/H[2,2],H2/H2[2,2],atol=1e-9,rtol=1e-9):
                raise ValueError(f"Calibration H duplicates {VIEW_LABELS[other_key]}; each mirror must use its OWN calibration.")

    def _load_direct_view_profile_dialog(self):
        super()._load_direct_view_profile_dialog()
        if not hasattr(self,"views") or self.direct_view_profile_path is None:return
        try:
            p=self.direct_view_profile_path
            profile=normalize_profile(json.loads(p.read_text(encoding="utf-8")),"direct")
            self._assert_independent(profile,"direct")
            self.views["direct"]["profile"]=profile
            self.views["direct"]["profile_path"]=str(p.resolve())
            zone=(profile.get("dynamic_tracking") or {}).get("search_roi_xyxy")
            if zone:self.views["direct"]["search_roi_xyxy"]=zone
            self._refresh_fields()
        except Exception as exc:self.status_var.set(f"v13 direct profile sync failed: {exc}")

    def _load_image(self,path):
        super()._load_image(path)
        if hasattr(self,"view_results"):
            self.view_results.clear();self.engines.clear();self.last_detected.clear()
            for v in self.views.values():v["previous_y"]=None
            self._reset_results_table()

    def _load_background_dialog(self):
        super()._load_background_dialog()
        if hasattr(self,"view_results"):
            self.view_results.clear();self.engines.clear();self.last_detected.clear()
            for v in self.views.values():v["previous_y"]=None
            self._reset_results_table()

    def _reset_results_table(self):
        if not hasattr(self,"result_table"):return
        for n in VIEW_NAMES:
            self.result_table.item(n,values=[VIEW_LABELS[n],"NOT RUN","—","—","—","—","—","—"])

    def _current_display_pil(self):
        out=super()._current_display_pil()
        if out is None or not hasattr(self,"views") or self.view_mode.get()!="original":return out
        d=ImageDraw.Draw(out)
        for n in VIEW_NAMES:
            v=self.views[n];c=PALETTE[n]
            roi=v.get("search_roi_xyxy")
            if roi:
                d.rectangle(roi,outline=c,width=4 if n==self._selected_name else 2)
                d.text((roi[0]+4,max(0,roi[1]-18)),f"{VIEW_LABELS[n]} SEARCH",fill=c)
            found=self.last_detected.get(n)
            if found:
                r=found["measurement_roi_xyxy"]
                d.rectangle(r,outline="#ff55cc",width=2)
        return out

    def _process(self):
        """Also routes the original v11/v12 PROCESS button to selected view."""
        if hasattr(self,"views"):
            self.process_selected()
        else:
            super()._process()

    def _require_photos(self):
        if self.image_cv is None or self.background_cv is None or self.image_cv_raw is None:
            raise ValueError("Load a WITH-filament image and a WITHOUT-filament background photograph.")
        if self.image_cv.shape != self.background_cv.shape:
            raise ValueError("Current and background image sizes do not match")

    def _process_one(self,n):
        v=self.views[n]
        if not v["search_roi_xyxy"]:
            self._put_failure(n,"SEARCH NOT SET","")
            return
        try:
            self._require_photos()
            profile=v["profile"]
            if profile is not None:
                cal=profile["local_planar_calibration"]
                actual=self.image_cv_raw.shape[:2]
                expected=(int(cal["original_image_height_px"]),int(cal["original_image_width_px"]))
                if actual!=expected:
                    # Still permit genuine pixel measurement on a resized PREVIEW.
                    # Pass profile=None: using H/K_ref here would be physically wrong.
                    preview_view=dict(v,profile=None)
                    found,meas,engine=process_view(self.image_cv,self.background_cv,
                                                  self.image_cv_raw.shape,preview_view)
                    meas["calibration_status"]="RESOLUTION MISMATCH: PREVIEW PIXEL ONLY"
                    meas["note"]="Original photo resolution required for physical mm."
                    self.last_detected[n]=found
                    v["previous_y"]=found["center_y"]
                    self.view_results[n]=meas
                    self.engines[n]=engine
                    self._put_success(n,meas)
                    return
            found,meas,engine=process_view(
                self.image_cv,self.background_cv,self.image_cv_raw.shape,v)
            v["previous_y"]=found["center_y"]
            self.last_detected[n]=found
            self.view_results[n]=meas
            self.engines[n]=engine
            self._put_success(n,meas)
        except Exception as exc:
            try:
                found=locate_view(self.image_cv,self.background_cv,v,v.get("profile"))
                self.last_detected[n]=found
                v["previous_y"]=found["center_y"]
                self._put_failure(n,"EDGE FAILED",str(exc),found)
            except Exception:
                self.last_detected.pop(n,None)
                self._put_failure(n,"NOT DETECTED",str(exc))

    def _put_failure(self,n,state,detail,found=None):
        self.view_results.pop(n,None);self.engines.pop(n,None)
        conf=f"{heuristic_confidence(found):.2f}" if found else "—"
        roi=str(found['measurement_roi_xyxy']) if found else "—"
        self.result_table.item(n,values=[VIEW_LABELS[n],state,roi,"—","—","—","—",conf])
        self.detail_var.set(f"{VIEW_LABELS[n]} | {state} | {detail}")

    def _put_success(self,n,meas):
        physical=meas["physical_stats"]
        s=physical if physical is not None else meas["pixel_stats"]
        unit="mm" if physical is not None else "px"
        fmt=lambda k:f"{s[k]:.5f}{unit}" if unit=="mm" else f"{s[k]:.3f}{unit}"
        state=("CALIBRATED" if physical is not None else "PIXEL ONLY")
        val=f"{meas['valid_slices']}/{meas['total_slices']}"
        self.result_table.item(n,values=[VIEW_LABELS[n],state,str(meas["roi"]),val,
                                          fmt("median"),fmt("mean"),fmt("std"),
                                          f"{meas['confidence_heuristic']:.2f}"])
        self.detail_var.set(
            f"{VIEW_LABELS[n]} | {val} slices | pixel median="
            f"{meas['pixel_stats']['median']:.3f}px | {meas['calibration_status']} "
            f"| confidence={meas['confidence_heuristic']:.2f} (quality heuristic). {meas['note']}")

    def process_selected(self):
        self._commit_fields()
        self._process_one(self._selected_name)
        self.view_mode.set("original")
        self._redraw()

    def process_all(self):
        self._commit_fields()
        for n in VIEW_NAMES:
            self._process_one(n)
        states=[f"{VIEW_LABELS[n]}: " + str(self.result_table.item(n,"values")[1]) for n in VIEW_NAMES]
        self.status_var.set(" | ".join(states))
        self.view_mode.set("original")
        self._redraw()

    def _selected_diagnostic(self):
        e=self.engines.get(self._selected_name)
        if e is None:
            messagebox.showwarning("No slices", "Process this view successfully before opening diagnostics.")
            return
        needed=["rectified_gray","valid","top_edges","bottom_edges","diameters_mm",
                "top_candidate_peaks_by_col","bottom_candidate_peaks_by_col",
                "rejected_peaks_by_col","kept_peaks_by_col","intensity_crossing_by_col"]
        for name in needed:setattr(self,name,getattr(e,name))
        self.gradient_multiplier.set(float(e.gradient_multiplier.get()))
        self.gradient_threshold.set(float(e.gradient_threshold.get()))
        self.use_signed_gradient.set(True)
        self.use_intensity_crossing.set(True)
        PaperMethodDiameterUI._show_slice_diagnostic(self)

    def _calibrate_rod(self):
        self._commit_fields()
        key=self._selected_name;v=self.views[key]
        try:
            self._require_photos()
            if v["profile"] is None:raise ValueError("Import a view-specific planar calibration first")
            known=float(self.known_width_var.get())
            if known<=0:raise ValueError("Known rod diameter must be positive")
            found,meas,engine=process_view(self.image_cv,self.background_cv,
                self.image_cv_raw.shape,v,operation="calibrate",known_mm=known)
            k=meas["K_ref"]
            if k is None or not np.isfinite(k) or k<=0:raise ValueError("Cannot derive K_ref")
            v["profile"]["target_plane_reference_correction"]={
                "enabled":True,"correction_factor_K_ref":float(k),
                "record":engine.reference_calibration_record,
                "relation":"D_final = D_homography * K_ref",
                "interpretation":"Effective calibration only at this view's reference-rod plane",
            }
            self.last_detected[key]=found
            v["previous_y"]=found["center_y"]
            self.status_var.set(f"{VIEW_LABELS[key]}: K_ref={k:.9f} from {known:.4f} mm rod. "
                                "EXPORT view calibration profile now. This is NOT independent accuracy validation.")
            self._refresh_fields()
        except Exception as exc:messagebox.showerror("Reference calibration failed",str(exc))

    def _export_profile(self):
        key=self._selected_name;v=self.views[key]
        if v["profile"] is None:
            messagebox.showwarning("No profile","Import an independent planar homography first.")
            return
        p=filedialog.asksaveasfilename(title="Save selected view calibration profile",
             initialfile=f"{key}_view_calibration_v13.json",defaultextension=".json",filetypes=[("JSON","*.json")])
        if p:
            Path(p).write_text(json.dumps(v["profile"],indent=2),encoding="utf-8")
            v["profile_path"]=str(Path(p).resolve())
            self._refresh_fields()

    def _save_config(self):
        self._commit_fields()
        p=filedialog.asksaveasfilename(initialfile="filament_three_view_v13_config.json",
             defaultextension=".json",filetypes=[("JSON","*.json")])
        if not p:return
        root=Path(p)
        conf={"schema":"filament_three_view_v13","working_rotation_degrees_clockwise":90,
              "views":{k:{x:y for x,y in v.items() if x!="previous_y"} for k,v in self.views.items()}}
        for v in conf["views"].values():
            if v["profile_path"]:
                try:v["profile_path"]=str(Path(v["profile_path"]).relative_to(root.parent))
                except ValueError:pass
        root.write_text(json.dumps(conf,indent=2),encoding="utf-8")
        self.status_var.set(f"Saved v13 config: {root.name}")

    def _load_config(self):
        p=filedialog.askopenfilename(filetypes=[("JSON","*.json")])
        if not p:return
        try:
            obj=json.loads(Path(p).read_text(encoding="utf-8"))
            if obj.get("schema")!="filament_three_view_v13":raise ValueError("Wrong v13 config schema")
            fresh={}
            for n in VIEW_NAMES:
                cfg=obj["views"][n]
                v=new_view(n)
                for k in v:
                    if k in cfg and k!="previous_y":v[k]=cfg[k]
                if v["polarity"] not in ("bright","dark"):raise ValueError("Unknown polarity")
                if v["profile"] is not None:
                    v["profile"]=normalize_profile(v["profile"],n)
                if v["profile_path"] and not Path(v["profile_path"]).is_absolute():
                    v["profile_path"]=str(Path(p).parent / v["profile_path"])
                fresh[n]=v
            for n in VIEW_NAMES:
                if fresh[n]["profile"] is not None:
                    H=np.asarray(fresh[n]["profile"]["local_planar_calibration"]["homography_original_camera_px_to_mm"],float)
                    for k in VIEW_NAMES:
                        if k==n or fresh[k]["profile"] is None:continue
                        J=np.asarray(fresh[k]["profile"]["local_planar_calibration"]["homography_original_camera_px_to_mm"],float)
                        if np.allclose(H/H[2,2],J/J[2,2],rtol=1e-9,atol=1e-9):
                            raise ValueError("Duplicate calibrated H across views")
            self.views=fresh
            self.engines.clear();self.view_results.clear();self.last_detected.clear()
            self._reset_results_table();self._refresh_fields();self._redraw()
            self.status_var.set(f"Loaded v13 configuration: {Path(p).name}")
        except Exception as exc:messagebox.showerror("Configuration invalid",str(exc))

    def _export_results(self):
        if not self.view_results:
            messagebox.showwarning("No results","Run a measurement first.")
            return
        p=filedialog.asksaveasfilename(initialfile="three_view_measurement_v13.json",
             defaultextension=".json",filetypes=[("JSON","*.json")])
        if not p:return
        data={"schema":"filament_three_view_results_v13",
              "frame":str(self.image_path) if self.image_path else None,
              "background":str(self.background_path) if self.background_path else None,
              "confidence_meaning":"Uncalibrated heuristic quality score, NOT probability of correctness",
              "angle_or_roundness":"NOT computed",
              "views":{n:{"polarity":self.views[n]["polarity"],
                          "profile_path":self.views[n]["profile_path"],
                          "localization":{k:v for k,v in self.last_detected[n].items()
                           if k not in ("difference_profile","difference_binary")},
                          "measurement":m} for n,m in self.view_results.items()}}
        Path(p).write_text(json.dumps(data,indent=2,default=lambda o: float(o) if isinstance(o,np.floating) else int(o)),encoding="utf-8")
        self.status_var.set(f"Exported three-view summary: {Path(p).name}")

    def _export_csv(self):
        if not self.engines:
            messagebox.showwarning("No slices","Process at least one view.")
            return
        p=filedialog.asksaveasfilename(initialfile="three_view_slices_v13.csv",
             defaultextension=".csv",filetypes=[("CSV","*.csv")])
        if not p:return
        with open(p,"w",encoding="utf-8",newline="") as f:
            writer=csv.writer(f)
            writer.writerow(["view","slice_x_in_roi","valid","top_edge_px","bottom_edge_px",
                             "diameter_px","diameter_mm_final_if_calibrated","K_ref_if_calibrated"])
            for n in VIEW_NAMES:
                if n not in self.engines:continue
                e=self.engines[n]
                calibrated=self.view_results[n]["physical_stats"] is not None
                for x,ok in enumerate(e.valid):
                    writer.writerow([VIEW_LABELS[n],x,bool(ok),
                                     float(e.top_edges[x]) if ok else "",
                                     float(e.bottom_edges[x]) if ok else "",
                                     float(e.widths_px[x]) if ok else "",
                                     float(e.diameters_mm[x]) if (ok and calibrated) else "",
                                     float(e.reference_correction_factor) if calibrated else ""])
        self.status_var.set(f"Exported three-view slices: {Path(p).name}")


if __name__=="__main__":
    ThreeViewUI().mainloop()

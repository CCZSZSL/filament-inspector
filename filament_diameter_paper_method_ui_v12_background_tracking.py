#!/usr/bin/env python3
"""v12 experimental Direct View: background-assisted dynamic ROI + unchanged v11 metrology.

Requires filament_diameter_paper_method_ui_v11_fixed_zone.py in the same folder.
The search image and background must have identical resolution, registration,
exposure/focus and illumination.  Do not use a GUI screenshot as a reference.

Only the DIRECT view has calibrated physical units in the supplied v11 profile.
Mirror views require their own homography and reference correction first.
"""
from __future__ import annotations

import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw

from filament_diameter_paper_method_ui_v11_fixed_zone import (
    PaperMethodDiameterUI, apply_homography_points,
)


def find_filament_by_background(
    frame_gray: np.ndarray,
    background_gray: np.ndarray,
    search_xyxy: tuple[int, int, int, int],
    *,
    width_range_mm=(1.3, 2.35),
    mm_per_px: float,
    foreground_threshold: float = 14.0,
    edge_padding_px: int = 14,
    expected_width_mm: float = 1.75,
    polarity: str = "bright",
    previous_y: float | None = None,
):
    """Locate horizontal filament in a vertical search strip (90° CW image).

    Background subtraction is used for LOCATION ONLY. The returned ROI must be
    processed on the *unaltered current grayscale image* by the original v11
    signed gradient / 50% intensity-crossing method.

    Inputs are full-size, already 90° CW grayscale images. Pixel coordinate
    bounds are inclusive. A 1D foreground profile is the median across x columns.
    Only contiguous foreground segments matching the physical width range are
    accepted; tiny background misregistration edges cannot resemble a filament.
    """
    if frame_gray.ndim != 2 or background_gray.shape != frame_gray.shape:
        raise ValueError("Current and background must be matching grayscale images.")
    if not np.isfinite(mm_per_px) or mm_per_px <= 0:
        raise ValueError("Invalid local scale for width gating.")
    x0, y0, x1, y1 = [int(v) for v in search_xyxy]
    hfull, wfull = frame_gray.shape
    if not (0 <= x0 < x1 < wfull and 0 <= y0 < y1 < hfull):
        raise ValueError("Search zone lies outside the rotated working image.")
    if x1-x0+1 < 12 or y1-y0+1 < 30:
        raise ValueError("Search zone too small (need >=12 columns, >=30 rows).")
    if polarity not in ("bright", "dark"):
        raise ValueError("Polarity must be bright or dark.")
    sign = 1.0 if polarity == "bright" else -1.0
    current = frame_gray[y0:y1+1, x0:x1+1].astype(np.int16)
    bg = background_gray[y0:y1+1, x0:x1+1].astype(np.int16)
    # Photometric baseline shift estimated from the majority background rows;
    # this compensates small uniform changes in lighting without moving edges.
    delta = sign * np.median((current-bg).astype(np.float32), axis=1)
    baseline = float(np.median(delta))
    centered = delta - baseline
    smooth = cv2.GaussianBlur(centered[:, None].astype(np.float32), (1, 5), 0).ravel()
    quiet_mad = float(np.median(np.abs(centered)))
    threshold = float(max(foreground_threshold, 4.0 * 1.4826 * quiet_mad))
    # Avoid allowing a huge foreground area (>half the search height) to
    # determine the threshold. Such an area is intrinsically ambiguous.
    if threshold >= max(70.0, float(np.max(smooth)) * 0.9):
        raise ValueError("Foreground/background contrast is insufficient or search zone is too small.")
    mask = (smooth > threshold).astype(np.uint8).reshape((-1,1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 1), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 1), np.uint8))
    binary = mask.ravel().astype(bool)
    transitions = np.diff(np.r_[False, binary, False].astype(np.int8))
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)  # exclusive
    wmin, wmax = [float(v) for v in width_range_mm]
    if not (0 < wmin <= wmax):
        raise ValueError("Invalid allowed filament diameter range.")
    # Foreground difference edges are approximate: tolerate 25% erosion/halo,
    # then use the original grayscale for accurate 50% edge placement.
    min_span = max(10, 0.75 * wmin / mm_per_px)
    max_span = 1.25 * wmax / mm_per_px
    candidates = []
    for a,b in zip(starts, ends):
        span = b-a
        if not min_span <= span <= max_span:
            continue
        # Signed contrast must be positive on the filament interior relative
        # to background. Coherence across columns rejects isolated dust spots.
        raw_patch = sign*(current[a:b,:] - bg[a:b,:])
        coverage = float(np.mean(raw_patch > (baseline + threshold)))
        if coverage < 0.50:
            continue
        center_y = float(y0 + (a+b-1)/2.0)
        approx_width = span*mm_per_px
        contrast = float(np.median(smooth[a:b]))
        cost = abs(approx_width-expected_width_mm)/max(expected_width_mm, 1e-6)
        cost += 0.4*(1.0-coverage)
        if previous_y is not None:
            cost += 0.4*abs(center_y-previous_y)/max(1.0, wmax/mm_per_px)
        candidates.append((cost, a,b, center_y, approx_width,contrast,coverage))
    if not candidates:
        raise ValueError(
            f"No credible filament found in search strip ({len(starts)} changed bands, "
            f"foreground threshold {threshold:.1f}). Keep the filament inside the "
            "search zone and verify background/lighting matches."
        )
    candidates.sort(key=lambda x:x[0])
    _,a,b, center, approx, contrast,coverage = candidates[0]
    pad=max(10,int(edge_padding_px))
    yy0=max(y0,int(y0+a)-pad)
    yy1=min(y1,int(y0+b-1)+pad)
    # Background plateaus must exist beyond both physical edges for 50% crossing.
    if yy0==y0 and a<pad or yy1==y1 and (len(binary)-b)<pad:
        raise ValueError("Filament too close to SEARCH zone boundary; expand the search vertically.")
    return {
        "measurement_roi_xyxy": [x0, yy0, x1, yy1],
        "foreground_rough_edge_y": [int(y0+a), int(y0+b-1)],
        "center_y": center,
        "foreground_width_mm_approx": float(approx),
        "foreground_contrast": contrast,
        "foreground_column_coverage": float(coverage),
        "adaptive_threshold_gray": threshold,
        "candidates": len(candidates),
        "baseline_gray": baseline,
        "difference_profile": smooth,
        "difference_binary": binary,
        "search_xyxy": [x0,y0,x1,y1],
    }


class BackgroundTrackingUI(PaperMethodDiameterUI):
    def _build_ui(self):
        super()._build_ui()
        self.title("Filament Diameter v12 — Background-Assisted Dynamic ROI (Direct View)")
        self.use_auto_location = tk.BooleanVar(value=False)
        self.foreground_threshold = tk.DoubleVar(value=14.0)
        self.edge_padding = tk.IntVar(value=14)
        self.min_diameter = tk.DoubleVar(value=1.3)
        self.max_diameter = tk.DoubleVar(value=2.35)
        self.expected_diameter = tk.DoubleVar(value=1.75)
        self.search_half_span = tk.IntVar(value=150)
        self.search_zone_xyxy = None
        self.last_tracking = None
        self.prev_track_y = None
        self.tracking_status_var = tk.StringVar(value="Search strip not configured")
        rbox = self.controls_frame.grid_slaves(row=3, column=0)[0]
        ttk.Separator(rbox,orient="horizontal").grid(row=9,column=0,columnspan=2,sticky="ew",pady=(11,7))
        ttk.Label(rbox,text="v12: Background-assisted auto-location",font=("Helvetica",12,"bold")).grid(row=10,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(rbox,text="Auto-locate filament before measurement",
                        variable=self.use_auto_location,command=self._auto_toggle).grid(row=11,column=0,columnspan=2,sticky="w",pady=5)
        ttk.Button(rbox,text="Set current 4 points as SEARCH area",
                   command=self._store_search_zone).grid(row=12,column=0,columnspan=2,sticky="ew")
        ttk.Button(rbox,text="Start from fixed-zone X, extend Y",
                   command=self._make_default_search_zone).grid(row=13,column=0,columnspan=2,sticky="ew",pady=5)
        ttk.Label(rbox,text="Search Y: ± pixels around saved ROI").grid(row=14,column=0,sticky="w")
        ttk.Spinbox(rbox,from_=50,to=700,increment=20,width=8,
                    textvariable=self.search_half_span).grid(row=14,column=1,sticky="e")
        ttk.Label(rbox,text="Foreground difference threshold (gray)").grid(row=15,column=0,sticky="w")
        ttk.Spinbox(rbox,from_=5,to=100,increment=2,width=8,
                    textvariable=self.foreground_threshold).grid(row=15,column=1,sticky="e")
        ttk.Label(rbox,text="Edge padding (px)").grid(row=16,column=0,sticky="w")
        ttk.Spinbox(rbox,from_=10,to=50,increment=2,width=8,
                    textvariable=self.edge_padding).grid(row=16,column=1,sticky="e")
        ttk.Label(rbox,text="Allowed physical width (mm)").grid(row=17,column=0,sticky="w")
        dims = ttk.Frame(rbox)
        dims.grid(row=17,column=1,sticky="e")
        ttk.Spinbox(dims,from_=0.5,to=3.0,increment=0.05,width=5,
                    textvariable=self.min_diameter).pack(side="left")
        ttk.Label(dims,text="–").pack(side="left")
        ttk.Spinbox(dims,from_=0.6,to=3.5,increment=0.05,width=5,
                    textvariable=self.max_diameter).pack(side="left")
        ttk.Label(rbox,text="Nominal width for selecting candidate (mm)").grid(row=18,column=0,sticky="w")
        ttk.Spinbox(rbox,from_=0.5,to=3.5,increment=0.05,width=8,
                    textvariable=self.expected_diameter).grid(row=18,column=1,sticky="e")
        ttk.Label(rbox,textvariable=self.tracking_status_var,wraplength=330).grid(row=19,column=0,columnspan=2,sticky="w",pady=(5,1))
        ttk.Button(rbox,text="Inspect background difference (after Process)",
                   command=self._show_foreground_debug).grid(row=20,column=0,columnspan=2,sticky="ew",pady=5)
        ttk.Label(rbox,wraplength=330,text=(
            "Workflow: load v11 profile + matching background photograph. "
            "Draw a wide-Y search area (clean X band) or start from saved ROI 3. "
            "The final v11 50% edges are measured on ORIGINAL current pixels, "
            "not on the difference image. Direct View only in this prototype."
        )).grid(row=21,column=0,columnspan=2,sticky="w")

    def _auto_toggle(self):
        if self.use_auto_location.get():
            self.use_fixed_roi.set(False)
            self.use_defect_mask.set(False)  # no hard rejection of true filament edges
            self.status_var.set("Auto ROI enabled: load background, configure SEARCH region; then Process.")
        else:
            self.status_var.set("Auto ROI disabled. v11 manual/fixed ROI processing is available.")

    def _store_search_zone(self):
        if self.image_cv is None or len(self.roi_points)!=4:
            messagebox.showwarning("Search zone","Load an image and select 4 corners first.")
            return
        self.search_zone_xyxy = list(self._roi_bounds())
        self.tracking_status_var.set(f"SEARCH area: {self.search_zone_xyxy} (90° CW pixels)")
        self.use_auto_location.set(True)
        self._auto_toggle()

    def _make_default_search_zone(self):
        if self.image_cv is None:
            messagebox.showwarning("Search zone","Load a full-resolution image first.")
            return
        if not self.fixed_roi_points:
            messagebox.showwarning("Search zone","Load a v11 Direct View Profile with fixed ROI first.")
            return
        pts=np.asarray(self.fixed_roi_points,dtype=float)
        h,w=self.image_cv.shape[:2]
        x0=max(0,int(math.floor(pts[:,0].min()))-3)
        x1=min(w-1,int(math.ceil(pts[:,0].max()))+3)
        ym=0.5*(pts[:,1].min()+pts[:,1].max())
        span=max(50,int(self.search_half_span.get()))
        y0=max(0,int(ym-span))
        y1=min(h-1,int(ym+span))
        self.search_zone_xyxy=[x0,y0,x1,y1]
        self.tracking_status_var.set(f"SEARCH area: {self.search_zone_xyxy} (from fixed ROI)")
        self.use_auto_location.set(True)
        self._auto_toggle()

    def _local_mm_per_px(self, cx, cy):
        if self.homography_working_px_to_mm is None:
            raise ValueError("Load Direct View calibration profile first.")
        points=apply_homography_points(
            self.homography_working_px_to_mm,
            np.array([[cx,cy],[cx,cy+1.0]],dtype=float),
        )
        k=float(self.reference_correction_factor_var.get()) if self.use_reference_correction.get() else 1.0
        return float(np.linalg.norm(points[1]-points[0])*k)

    def _run_auto_location(self):
        if self.image_cv is None or self.background_cv is None:
            raise ValueError("Load BOTH the current filament image and a no-filament background PHOTO.")
        if self.image_cv.shape != self.background_cv.shape:
            raise ValueError("Current image and background have different dimensions.")
        if self.search_zone_xyxy is None:
            raise ValueError("Set a tall SEARCH area first (or use fixed-zone X and extend Y).")
        x0,y0,x1,y1=self.search_zone_xyxy
        if x0<0 or y0<0 or x1>=self.image_cv.shape[1] or y1>=self.image_cv.shape[0]:
            raise ValueError("Stored search area is outside the current image.")
        mm_per_px=self._local_mm_per_px(0.5*(x0+x1),0.5*(y0+y1))
        cgray=cv2.cvtColor(self.image_cv,cv2.COLOR_BGR2GRAY)
        bgray=cv2.cvtColor(self.background_cv,cv2.COLOR_BGR2GRAY)
        found=find_filament_by_background(
            cgray,bgray,tuple(self.search_zone_xyxy),
            mm_per_px=mm_per_px,
            width_range_mm=(float(self.min_diameter.get()),float(self.max_diameter.get())),
            foreground_threshold=float(self.foreground_threshold.get()),
            edge_padding_px=int(self.edge_padding.get()),
            expected_width_mm=(float(self.known_diameter_mm.get()) if self.operation_mode.get()=="calibrate" else float(self.expected_diameter.get())),
            polarity="bright",
            previous_y=self.prev_track_y,
        )
        self.last_tracking=found
        self.prev_track_y=found["center_y"]
        a,b,c,d=found["measurement_roi_xyxy"]
        self.use_fixed_roi.set(False)
        self.roi_points=[(float(a),float(b)),(float(c),float(b)),(float(c),float(d)),(float(a),float(d))]
        self.roi_info_var.set("4 / 4 points (AUTO-DYNAMIC)")
        self._invalidate_processing()
        self.view_mode.set("original")
        self.tracking_status_var.set(
            f"AUTO center y={found['center_y']:.1f} | rough width={found['foreground_width_mm_approx']:.3f} mm "
            f"| candidates={found['candidates']} | threshold={found['adaptive_threshold_gray']:.1f} "
            f"| ROI {found['measurement_roi_xyxy']}"
        )
        self._redraw()
        return found

    def _process(self):
        if self.use_auto_location.get():
            try:
                self._run_auto_location()
            except Exception as exc:
                self.last_tracking=None
                self.tracking_status_var.set(f"NOT DETECTED: {exc}")
                self.status_var.set("No reliable filament location; measurement rejected. Check background alignment/search area.")
                messagebox.showwarning("Automatic filament location",str(exc))
                return
        super()._process()
        if self.use_auto_location.get() and self.diameters_mm is not None and self.last_tracking is not None:
            note=(f"\n\nBACKGROUND ASSISTED AUTO ROI\n"
                  f"Search: {self.search_zone_xyxy}\n"
                  f"Dynamic ROI: {self.last_tracking['measurement_roi_xyxy']}\n"
                  f"Foreground candidate width (coarse only): {self.last_tracking['foreground_width_mm_approx']:.3f} mm\n"
                  f"Actual final width: see DIAMETER (FINAL) above")
            self.result_var.set(self.result_var.get()+note)

    def _load_direct_view_profile_dialog(self):
        super()._load_direct_view_profile_dialog()
        p=self.direct_view_profile_path
        if p is None or not p.is_file():
            return
        try:
            cfg=json.loads(p.read_text(encoding="utf-8")).get("dynamic_tracking",{})
            if not cfg:
                return
            self.search_zone_xyxy=cfg.get("search_roi_xyxy")
            if self.search_zone_xyxy:
                self.tracking_status_var.set(f"SEARCH area restored: {self.search_zone_xyxy}")
            self.foreground_threshold.set(float(cfg.get("min_foreground_difference",14)))
            self.edge_padding.set(int(cfg.get("measurement_background_padding_px",14)))
            self.min_diameter.set(float(cfg.get("allowed_width_mm",[1.3,2.35])[0]))
            self.max_diameter.set(float(cfg.get("allowed_width_mm",[1.3,2.35])[1]))
            self.expected_diameter.set(float(cfg.get("expected_width_mm",1.75)))
            self.use_auto_location.set(bool(cfg.get("enabled",False)))
            if self.use_auto_location.get():
                self._auto_toggle()
            rel=cfg.get("background_image_path")
            if rel:
                candidate=Path(rel)
                if not candidate.is_absolute():
                    candidate=p.parent/candidate
                if candidate.is_file():
                    im=cv2.imread(str(candidate),cv2.IMREAD_COLOR)
                    if im is not None:
                        rotated=cv2.rotate(im,cv2.ROTATE_90_CLOCKWISE)
                        if self.image_cv is None or self.image_cv.shape==rotated.shape:
                            self.background_cv_raw=im
                            self.background_cv=rotated
                            self.background_path=candidate
                            self.background_info_var.set(f"Background: {candidate.name} | 90° CW")
                elif self.use_auto_location.get():
                    self.tracking_status_var.set("Search area restored; background file missing. Load background manually.")
        except Exception as exc:
            self.tracking_status_var.set(f"Dynamic-tracking settings not restored: {exc}")

    def _build_direct_view_profile(self):
        profile=super()._build_direct_view_profile()
        background_path=None
        if self.background_path:
            background_path=str(self.background_path)
            if self.direct_view_profile_path:
                try:
                    background_path=str(self.background_path.relative_to(self.direct_view_profile_path.parent))
                except ValueError:
                    pass
        profile["dynamic_tracking"]={
            "enabled":bool(self.use_auto_location.get()),
            "coordinate_system":"90deg_clockwise_working_image_px",
            "search_roi_xyxy":self.search_zone_xyxy,
            "background_image_path":background_path,
            "min_foreground_difference":float(self.foreground_threshold.get()),
            "measurement_background_padding_px":int(self.edge_padding.get()),
            "allowed_width_mm":[float(self.min_diameter.get()),float(self.max_diameter.get())],
            "expected_width_mm":float(self.expected_diameter.get()),
            "background_is_for_localization_only":True,
            "note":"Keep one calibration profile JSON and one aligned background photo. Not an absolute static-edge ban.",
        }
        return profile

    def _save_direct_view_profile(self):
        try:
            profile=self._build_direct_view_profile()
        except Exception as exc:
            messagebox.showerror("Cannot save profile",str(exc))
            return
        p=filedialog.asksaveasfilename(
            title="Save v12 Direct View profile",
            defaultextension=".json",
            filetypes=[("JSON","*.json")],
            initialfile="direct_view_calibration_v12.json",
        )
        if not p:
            return
        # Store a portable relative background photo name when side-by-side.
        if self.background_path and self.background_path.parent.resolve()==Path(p).parent.resolve():
            profile["dynamic_tracking"]["background_image_path"]=self.background_path.name
        Path(p).write_text(json.dumps(profile,indent=2),encoding="utf-8")
        self.direct_view_profile_path=Path(p)
        self.status_var.set(f"v12 profile saved: {Path(p).name}")

    def _show_foreground_debug(self):
        d=self.last_tracking
        if d is None or self.background_cv is None or self.image_cv is None:
            messagebox.showinfo("Difference preview","Run automatic localization first.")
            return
        x0,y0,x1,y1=d["search_xyxy"]
        c=cv2.cvtColor(self.image_cv[y0:y1+1,x0:x1+1],cv2.COLOR_BGR2GRAY).astype(np.int16)
        b=cv2.cvtColor(self.background_cv[y0:y1+1,x0:x1+1],cv2.COLOR_BGR2GRAY).astype(np.int16)
        diff=np.clip(np.abs(c-b)*3,0,255).astype(np.uint8)
        rgb=cv2.cvtColor(diff,cv2.COLOR_GRAY2RGB)
        a,b0,c1,d1=d["measurement_roi_xyxy"]
        cv2.rectangle(rgb,(0,b0-y0),(rgb.shape[1]-1,d1-y0),(0,255,0),1)
        img=Image.fromarray(rgb).resize((max(250,rgb.shape[1]*8),min(900,rgb.shape[0]*2)),Image.Resampling.NEAREST)
        win=tk.Toplevel(self)
        win.title("v12 | background difference (green = dynamically selected measurement ROI)")
        photo=ImageTk.PhotoImage(img)
        label=ttk.Label(win,image=photo)
        label.image=photo
        label.pack(padx=8,pady=8)
        ttk.Label(win,text="Difference image is for localization ONLY; original image supplies subpixel edge positions.").pack(padx=8,pady=5)


if __name__=="__main__":
    BackgroundTrackingUI().mainloop()

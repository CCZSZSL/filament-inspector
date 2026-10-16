#!/usr/bin/env python3
"""
Paper-Method Filament Diameter UI v11
=================================

Implements the diameter-extraction method described by:
A. L. Petsiuk and J. M. Pearce,
"Open Source Filament Diameter Sensor for Recycling, Winding and
Additive Manufacturing Machines", J. Manuf. Sci. Eng. (2021).

Core paper method implemented here
----------------------------------
1. Manually define a rectangular Region of Interest (ROI).
2. Rotate the full camera frame 90° clockwise to create one fixed working view.
   The filament runs left-to-right; its original left/right edges become top/bottom.
3. Crop the ROI from that rotated working image. No perspective warp is applied.
4. Convert the ROI to grayscale and process every VERTICAL one-pixel column independently.
5. For each vertical grayscale profile f(p_i), calculate the paper gradient:

       g(p_i) = m * | f(p_i+1) - f(p_i-1) |

   with m = 15 by default (paper value).

6. Baseline paper mode detects LOCAL peaks of the absolute gradient above an empirical threshold.
7. Optional engineering extension: preserve gradient polarity with
       h(p_i) = m * (f(p_i+1) - f(p_i-1))
   so the TOP edge is selected from positive (dark→bright) peaks and the
   BOTTOM edge from negative (bright→dark) peaks.
8. v7 engineering extension: signed gradient peaks locate a candidate transition
   region, but the physical edge is localized at the 50% intensity crossing:

       I_edge = (I_bg + I_rod) / 2

   with linear sub-pixel interpolation of the grayscale crossing.  The v6
   strongest-gradient peak localization remains available as a comparison mode.
9. The distance between the selected boundaries is the filament diameter in pixels.
10. The collection of one-pixel masks gives the segmented filament section.
11. Calculate its centreline and rectify the section by shifting slices so
    the centreline is aligned.
12. v8 converts every detected Top/Bottom edge pair from the fixed 90° CW
    working image back to ORIGINAL camera coordinates, then applies the saved
    local-planar homography:

       (x_original, y_original) -> (X_mm, Y_mm)

    The physical diameter of each single-pixel slice is then

       D_mm = sqrt((X_bottom-X_top)^2 + (Y_bottom-Y_top)^2)

    Pixel diameter is still reported for debugging/comparison, but no single
    global mm/px scale is used for the final diameter.  The v7 gradient, signed
    polarity, 50% intensity-crossing and v6 comparison logic are unchanged.

Dependencies (Mac host):
    python3 -m pip install opencv-python numpy pillow
"""

from __future__ import annotations

import csv
import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

from filament_inspector.algorithms.edges import order_quad_tl_tr_br_bl, quad_output_size, local_maxima_above, parabolic_subpixel_peak, linear_intensity_crossing, fifty_percent_edge_pair, robust_summary, apply_homography_points
from filament_inspector.algorithms.engine import PaperMethodEngine

class PaperMethodDiameterUI(PaperMethodEngine, tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Filament Diameter - Paper Gradient Method v11 (fixed measurement zone + unified calibration profile)")
        self.geometry("1580x950")
        self.minsize(1200, 760)

        # Measurement image
        self.image_path: Path | None = None
        # Raw camera frame is stored only for metadata. All display/ROI/processing uses
        # a fixed 90° clockwise working image so the filament runs left-to-right.
        self.image_cv_raw = None
        self.image_cv = None
        self.image_pil = None
        self.working_rotation_degrees_clockwise = 90

        # Static background reference for engineering defect rejection.
        # Capture this with the calibration rod / filament removed while keeping
        # camera, mirrors, focus, exposure and geometry unchanged.
        self.background_path: Path | None = None
        self.background_cv_raw = None
        self.background_cv = None
        self.background_info_var = tk.StringVar(value="No background reference loaded")

        self.use_defect_mask = tk.BooleanVar(value=False)
        self.defect_gradient_threshold = tk.DoubleVar(value=1200.0)
        self.defect_peak_radius_px = tk.IntVar(value=2)
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None

        # Engineering edge-selection extensions. The paper baseline remains
        # available by disabling these toggles.
        self.use_signed_gradient = tk.BooleanVar(value=True)
        # v7 default: gradient peaks define only the candidate transition region;
        # final edges come from the 50% grayscale crossing. Disable this checkbox
        # to compare directly against the v6 strongest-gradient peak method.
        self.use_intensity_crossing = tk.BooleanVar(value=True)
        self.use_subpixel = tk.BooleanVar(value=True)

        # Engineering continuity constraint applied AFTER candidate peaks
        # have been found.
        self.use_continuity = tk.BooleanVar(value=False)
        self.max_edge_jump_px = tk.DoubleVar(value=6.0)

        # ROI selection in the 90° clockwise ROTATED working-image pixels
        self.roi_points: list[tuple[float, float]] = []

        # v11 fixed measurement zone.  The final Direct View system intentionally
        # measures only one clean local window (ROI 3), rather than requiring the
        # whole field of view to behave identically.  The exact ROI is selected once
        # by the user, stored in the calibration profile, and automatically reused.
        self.use_fixed_roi = tk.BooleanVar(value=False)
        self.fixed_roi_points: list[tuple[float, float]] | None = None
        self.fixed_roi_info_var = tk.StringVar(value="Fixed zone: not set")
        self.direct_view_profile_path: Path | None = None

        # Display navigation
        self.preview_photo = None
        self.display_scale = 1.0
        self.display_offset_x = 0.0
        self.display_offset_y = 0.0
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._drag_start = None
        self._drag_pan_start = None

        self.tool_mode = tk.StringVar(value="point")
        self.view_mode = tk.StringVar(value="original")
        self.zoom_label_var = tk.StringVar(value="Zoom: 100%")

        # Paper parameters
        self.gradient_multiplier = tk.DoubleVar(value=15.0)  # paper m = 15
        self.gradient_threshold = tk.DoubleVar(value=40.0)   # empirical; user-tunable
        self.edge_margin_px = tk.IntVar(value=3)

        # Calibration / measurement (v8)
        # "measure" = normal filament measurement; "calibrate" is retained as a
        # validation mode for a known-width reference.  The reference width is NOT
        # used to create a scale; physical dimensions come only from the planar homography.
        self.operation_mode = tk.StringVar(value="measure")
        self.known_diameter_mm = tk.DoubleVar(value=2.000)
        self.planar_calibration_path: Path | None = None
        self.planar_calibration_data = None
        self.homography_original_px_to_mm = None
        self.homography_working_px_to_mm = None
        self.calibration_original_width_px = None
        self.calibration_original_height_px = None

        # v9 depth-plane correction. The local planar homography is valid on the
        # checkerboard plane. If the filament plane is farther from the camera by
        # delta_z, the homography-only diameter is too small. Under the local
        # pinhole / parallel-plane approximation:
        #     D_corrected = D_homography * Z_filament / Z_calibration
        #                 = D_homography * Zf / (Zf - delta_z)
        # Positive delta_z means the calibration board is CLOSER to the camera.
        self.use_depth_correction = tk.BooleanVar(value=True)
        self.filament_plane_distance_mm = tk.DoubleVar(value=85.0)
        self.calibration_plane_closer_by_mm = tk.DoubleVar(value=3.0)
        self.depth_info_var = tk.StringVar(value='Depth factor: 1.036585  (85 / 82)')

        # v11 target-plane correction seeded from the clean 2.000 mm rod ROI.
        # raw homography median = 1.96939 mm -> K_ref = 2 / 1.96939.
        self.use_reference_correction = tk.BooleanVar(value=True)
        self.reference_correction_factor_var = tk.DoubleVar(value=1.0155428838371272)
        self.reference_info_var = tk.StringVar(
            value="K_ref: 1.015543 | seeded from 2.000 mm / 1.96939 mm clean ROI"
        )
        self.reference_correction_factor = 1.0155428838371272
        self.reference_correction_path: Path | None = None
        self.reference_calibration_record = {
            "calibration_type": "target_plane_reference_correction",
            "known_width_mm": 2.000,
            "raw_homography_median_mm": 1.96939,
            "correction_factor": 1.0155428838371272,
            "valid_slices": 16,
            "pixel_diameter_median_px": 84.7404,
            "pixel_diameter_std_px": 0.1775,
            "source": "clean precision-rod ROI supplied 2026-10-10",
        }

        # Legacy names retained only so old UI internals do not break; v8 never uses
        # scale_mm_per_px for the final physical diameter.
        self.scale_mm_per_px: float | None = None
        self.scale_source_path: Path | None = None

        # Results / processed views
        self.rectified_gray = None
        self.segment_mask = None
        self.rectified_mask = None
        self.top_edges = None
        self.bottom_edges = None
        self.centers = None
        self.valid = None
        self.widths_px = None
        self.diameters_mm = None  # final v10 reference-corrected diameter when enabled
        self.diameters_mm_homography_raw = None
        self.depth_correction_factor = 1.0
        self.top_points_working_px = None
        self.bottom_points_working_px = None
        self.top_points_original_px = None
        self.bottom_points_original_px = None
        self.top_points_mm = None
        self.bottom_points_mm = None
        self.sample_profiles = {}
        self.raw_peaks_by_col = {}
        self.kept_peaks_by_col = {}
        self.rejected_peaks_by_col = {}
        self.signed_gradients_by_col = {}
        self.top_candidate_peaks_by_col = {}
        self.bottom_candidate_peaks_by_col = {}
        self.intensity_crossing_by_col = {}

        # Status
        self.image_info_var = tk.StringVar(value="No image loaded")
        self.roi_info_var = tk.StringVar(value="0 / 4 ROI points selected")
        self.scale_info_var = tk.StringVar(value="Planar calibration: not loaded")
        self.result_var = tk.StringVar(value="Processing: not calculated")
        self.status_var = tk.StringVar(
            value="Load a Direct View calibration profile or planar calibration, then select the clean fixed measurement zone."
        )

        self._build_ui()
        self._bind_events()
        self.reference_correction_factor_var.trace_add("write", lambda *_: self._reference_settings_changed())
        self._reference_settings_changed()

    # -----------------------------------------------------------------
    # Build UI
    # -----------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # Scrollable left control panel.
        # The full control stack is placed inside a Canvas so the lower
        # processing/result buttons remain reachable on smaller displays.
        controls_host = ttk.Frame(self)
        self.controls_host = controls_host
        controls_host.grid(row=0, column=0, sticky="ns")
        controls_host.rowconfigure(0, weight=1)
        controls_host.columnconfigure(0, weight=1)

        self.controls_scroll_canvas = tk.Canvas(
            controls_host,
            width=375,
            highlightthickness=0,
            borderwidth=0,
            background=self.cget("background"),
        )
        self.controls_scroll_canvas.grid(row=0, column=0, sticky="ns")

        self.controls_scrollbar = ttk.Scrollbar(
            controls_host,
            orient="vertical",
            command=self.controls_scroll_canvas.yview,
        )
        self.controls_scrollbar.grid(row=0, column=1, sticky="ns")
        self.controls_scroll_canvas.configure(yscrollcommand=self.controls_scrollbar.set)

        controls = ttk.Frame(self.controls_scroll_canvas, padding=14)
        self.controls_frame = controls
        controls.columnconfigure(0, weight=1)
        self.controls_window = self.controls_scroll_canvas.create_window(
            (0, 0),
            window=controls,
            anchor="nw",
        )

        controls.bind("<Configure>", self._controls_content_configure)
        self.controls_scroll_canvas.bind("<Configure>", self._controls_canvas_configure)

        main = ttk.Frame(self, padding=(0, 14, 14, 14))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        ttk.Label(
            controls,
            text="Paper-Method\nDiameter Sensor",
            font=("Helvetica", 21, "bold"),
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        # 1 Image
        fbox = ttk.LabelFrame(controls, text="1. Image", padding=10)
        fbox.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        fbox.columnconfigure(0, weight=1)

        ttk.Button(
            fbox,
            text="Load Full-Resolution Image...",
            command=self._load_image_dialog,
        ).grid(row=0, column=0, sticky="ew")

        ttk.Label(
            fbox,
            textvariable=self.image_info_var,
            wraplength=340,
        ).grid(row=1, column=0, sticky="w", pady=(5, 9))

        ttk.Button(
            fbox,
            text="Load Background Reference...",
            command=self._load_background_dialog,
        ).grid(row=2, column=0, sticky="ew")

        ttk.Label(
            fbox,
            textvariable=self.background_info_var,
            wraplength=340,
        ).grid(row=3, column=0, sticky="w", pady=(5, 0))

        # 2 Local planar calibration
        mbox = ttk.LabelFrame(controls, text="2. Local Planar Calibration", padding=10)
        mbox.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        mbox.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            mbox,
            text="Measure filament (apply reference correction)",
            value="measure",
            variable=self.operation_mode,
            command=self._mode_changed,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Radiobutton(
            mbox,
            text="Calibrate K_ref from known-width reference",
            value="calibrate",
            variable=self.operation_mode,
            command=self._mode_changed,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 5))

        ttk.Label(mbox, text="Reference width:").grid(row=2, column=0, sticky="w")
        self.known_spin = ttk.Spinbox(
            mbox,
            from_=0.1,
            to=20.0,
            increment=0.001,
            textvariable=self.known_diameter_mm,
            width=10,
            state="disabled",
        )
        self.known_spin.grid(row=2, column=1, sticky="e")
        ttk.Label(mbox, text="mm").grid(row=2, column=2, sticky="w", padx=(4, 0))

        ttk.Checkbutton(
            mbox,
            text="Apply target-plane reference correction",
            variable=self.use_reference_correction,
            command=self._reference_settings_changed,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(7, 2))

        ttk.Label(mbox, text="Reference factor K_ref:").grid(row=4, column=0, sticky="w")
        self.reference_factor_spin = ttk.Spinbox(
            mbox, from_=0.8, to=1.2, increment=0.000001,
            textvariable=self.reference_correction_factor_var, width=12,
            command=self._reference_settings_changed,
        )
        self.reference_factor_spin.grid(row=4, column=1, sticky="e")
        ttk.Label(mbox, text="×").grid(row=4, column=2, sticky="w", padx=(4, 0))

        ttk.Label(mbox, textvariable=self.reference_info_var, wraplength=340).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(4, 2)
        )

        profile_buttons = ttk.Frame(mbox)
        profile_buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        profile_buttons.columnconfigure(0, weight=1)
        profile_buttons.columnconfigure(1, weight=1)
        ttk.Button(
            profile_buttons, text="Load Direct View Profile...",
            command=self._load_direct_view_profile_dialog
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(
            profile_buttons, text="Save Direct View Profile...",
            command=self._save_direct_view_profile
        ).grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.load_scale_btn = ttk.Button(
            mbox,
            text="Import Local Planar Calibration JSON (setup)...",
            command=self._load_scale_dialog,
        )
        self.load_scale_btn.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(7, 0))

        ttk.Label(
            mbox,
            textvariable=self.scale_info_var,
            wraplength=340,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # 3 ROI
        rbox = ttk.LabelFrame(controls, text="3. Region of Interest", padding=10)
        rbox.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        rbox.columnconfigure(0, weight=1)
        rbox.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            rbox,
            text="Point Tool",
            value="point",
            variable=self.tool_mode,
            command=self._update_cursor,
        ).grid(row=0, column=0, sticky="w")

        ttk.Radiobutton(
            rbox,
            text="Pan Tool",
            value="pan",
            variable=self.tool_mode,
            command=self._update_cursor,
        ).grid(row=0, column=1, sticky="w")

        ttk.Label(
            rbox,
            text="① TL → ② TR → ③ BR → ④ BL",
            font=("Helvetica", 11, "bold"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))

        ttk.Label(
            rbox,
            textvariable=self.roi_info_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))

        ttk.Checkbutton(
            rbox,
            text="Use fixed measurement zone",
            variable=self.use_fixed_roi,
            command=self._fixed_roi_toggle_changed,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

        ttk.Label(
            rbox, textvariable=self.fixed_roi_info_var, wraplength=335
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))

        ttk.Button(
            rbox, text="Set Current ROI as Fixed Zone",
            command=self._set_current_roi_as_fixed,
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(7, 0))

        ttk.Button(
            rbox, text="Reapply Fixed Zone", command=self._apply_fixed_roi_to_current
        ).grid(row=6, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        ttk.Button(
            rbox, text="Clear Fixed Zone", command=self._clear_fixed_roi
        ).grid(row=6, column=1, sticky="ew", padx=(3, 0), pady=(5, 0))

        ttk.Button(
            rbox,
            text="Undo Last",
            command=self._undo_point,
        ).grid(row=7, column=0, sticky="ew", padx=(0, 3), pady=(7, 0))

        ttk.Button(
            rbox,
            text="Reset 4 Points",
            command=self._reset_points,
        ).grid(row=7, column=1, sticky="ew", padx=(3, 0), pady=(7, 0))

        ttk.Label(
            rbox,
            text=(
                "For final Direct View operation, select the clean ROI 3 once, set it as the fixed zone, "
                "calibrate K_ref there with the precision reference, then save one Direct View Profile JSON."
            ),
            wraplength=335,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(7, 0))

        # 4 Paper settings
        pbox = ttk.LabelFrame(controls, text="4. Paper Segmentation Parameters", padding=10)
        pbox.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        pbox.columnconfigure(1, weight=1)

        ttk.Label(pbox, text="Gradient multiplier m:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            pbox,
            from_=1.0,
            to=50.0,
            increment=1.0,
            textvariable=self.gradient_multiplier,
            width=9,
        ).grid(row=0, column=1, sticky="e")

        ttk.Label(pbox, text="Gradient threshold:").grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )
        ttk.Spinbox(
            pbox,
            from_=0.0,
            to=4000.0,
            increment=5.0,
            textvariable=self.gradient_threshold,
            width=9,
        ).grid(row=1, column=1, sticky="e", pady=(5, 0))

        ttk.Label(pbox, text="ROI edge margin:").grid(
            row=2, column=0, sticky="w", pady=(5, 0)
        )
        rr = ttk.Frame(pbox)
        rr.grid(row=2, column=1, sticky="e", pady=(5, 0))
        ttk.Spinbox(
            rr,
            from_=0,
            to=50,
            increment=1,
            textvariable=self.edge_margin_px,
            width=7,
        ).grid(row=0, column=0)
        ttk.Label(rr, text="px").grid(row=0, column=1, padx=(4, 0))

        ttk.Label(
            pbox,
            text=(
                "Paper equation: g(xᵢ)=m·|f(xᵢ₊₁)−f(xᵢ₋₁)|. "
                "m=15 is the paper value; threshold is empirical."
            ),
            wraplength=335,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))

        # 5 Engineering extensions (not part of the paper's original algorithm)
        ebox = ttk.LabelFrame(
            controls,
            text="5. Engineering Edge Selection",
            padding=10
        )
        ebox.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        ebox.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            ebox,
            text="Use signed gradient polarity",
            variable=self.use_signed_gradient,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Checkbutton(
            ebox,
            text="Use 50% intensity crossing (v7)",
            variable=self.use_intensity_crossing,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))

        ttk.Checkbutton(
            ebox,
            text="Use sub-pixel peak localization (v6 comparison)",
            variable=self.use_subpixel,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))

        ttk.Label(
            ebox,
            text=(
                "v7: signed gradient finds the Top/Bottom transition region; final "
                "edges are the 50% grayscale crossings with linear sub-pixel interpolation. "
                "Turn 50% crossing OFF to compare against the v6 strongest-gradient peak "
                "method; the parabolic sub-pixel checkbox then controls that comparison."
            ),
            wraplength=335,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Separator(ebox, orient="horizontal").grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=8
        )

        ttk.Checkbutton(
            ebox,
            text="Use static defect mask",
            variable=self.use_defect_mask,
        ).grid(row=5, column=0, columnspan=2, sticky="w")

        ttk.Label(ebox, text="Defect gradient threshold:").grid(
            row=6, column=0, sticky="w", pady=(5, 0)
        )
        ttk.Spinbox(
            ebox,
            from_=0.0,
            to=4000.0,
            increment=10.0,
            textvariable=self.defect_gradient_threshold,
            width=9,
        ).grid(row=6, column=1, sticky="e", pady=(5, 0))

        ttk.Label(ebox, text="Defect peak radius:").grid(
            row=7, column=0, sticky="w", pady=(5, 0)
        )
        drow = ttk.Frame(ebox)
        drow.grid(row=7, column=1, sticky="e", pady=(5, 0))
        ttk.Spinbox(
            drow,
            from_=0,
            to=30,
            increment=1,
            textvariable=self.defect_peak_radius_px,
            width=7,
        ).grid(row=0, column=0)
        ttk.Label(drow, text="px").grid(row=0, column=1, padx=(4, 0))

        ttk.Button(
            ebox,
            text="Build Defect Mask",
            command=self._build_defect_mask,
        ).grid(row=8, column=0, columnspan=2, sticky="ew", pady=(7, 0))

        ttk.Separator(ebox, orient="horizontal").grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=8
        )

        ttk.Checkbutton(
            ebox,
            text="Use edge continuity tracking",
            variable=self.use_continuity,
        ).grid(row=10, column=0, columnspan=2, sticky="w")

        ttk.Label(ebox, text="Max edge jump:").grid(
            row=11, column=0, sticky="w", pady=(5, 0)
        )
        jrow = ttk.Frame(ebox)
        jrow.grid(row=11, column=1, sticky="e", pady=(5, 0))
        ttk.Spinbox(
            jrow,
            from_=1.0,
            to=50.0,
            increment=0.5,
            textvariable=self.max_edge_jump_px,
            width=7,
        ).grid(row=0, column=0)
        ttk.Label(jrow, text="px").grid(row=0, column=1, padx=(4, 0))

        ttk.Label(
            ebox,
            text=(
                "All controls in this section are engineering extensions. "
                "Recommended Direct View settings: signed polarity ON, 50% crossing ON, "
                "static defect mask OFF, continuity OFF, ROI edge margin 3 px."
            ),
            wraplength=335,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(7, 0))

        # 6 Actions
        abox = ttk.LabelFrame(controls, text="6. Process", padding=10)
        abox.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        abox.columnconfigure(0, weight=1)

        ttk.Button(
            abox,
            text="PROCESS USING PAPER METHOD",
            command=self._process,
        ).grid(row=0, column=0, sticky="ew", ipady=7)

        ttk.Button(
            abox,
            text="Show Slice Diagnostic",
            command=self._show_slice_diagnostic,
        ).grid(row=1, column=0, sticky="ew", pady=(7, 0))

        ttk.Button(
            abox,
            text="Save Measurement Summary JSON",
            command=self._save_scale,
        ).grid(row=2, column=0, sticky="ew", pady=(7, 0))

        ttk.Button(
            abox,
            text="Save Measurements CSV",
            command=self._save_csv,
        ).grid(row=3, column=0, sticky="ew", pady=(7, 0))

        # Result
        resbox = ttk.LabelFrame(controls, text="Results", padding=10)
        resbox.grid(row=7, column=0, sticky="ew")
        resbox.columnconfigure(0, weight=1)

        ttk.Label(
            resbox,
            textvariable=self.result_var,
            wraplength=345,
            justify="left",
            font=("Menlo", 10),
        ).grid(row=0, column=0, sticky="w")

        # Main area
        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="Paper Method View",
            font=("Helvetica", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")

        for col, (label, value) in enumerate([
            ("Original", "original"),
            ("Defect Mask", "defect"),
            ("Segmented", "segmented"),
            ("Rectified", "rectified"),
        ], start=1):
            ttk.Radiobutton(
                header,
                text=label,
                value=value,
                variable=self.view_mode,
                command=self._view_changed,
            ).grid(row=0, column=col, padx=(10, 0))

        nav = ttk.Frame(main)
        nav.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        nav.columnconfigure(6, weight=1)

        ttk.Button(
            nav, text="−", width=4,
            command=lambda: self._zoom_step(1 / 1.25),
        ).grid(row=0, column=0, padx=(0, 3))
        ttk.Button(
            nav, text="+", width=4,
            command=lambda: self._zoom_step(1.25),
        ).grid(row=0, column=1, padx=3)
        ttk.Button(
            nav, text="Fit",
            command=self._fit_view,
        ).grid(row=0, column=2, padx=(6, 3))
        ttk.Button(
            nav, text="100%",
            command=self._actual_size_view,
        ).grid(row=0, column=3, padx=3)
        ttk.Label(
            nav,
            textvariable=self.zoom_label_var,
        ).grid(row=0, column=4, padx=(10, 0))
        ttk.Label(
            nav,
            text="Wheel: zoom | Pan Tool: drag",
        ).grid(row=0, column=6, sticky="e")

        self.canvas = tk.Canvas(
            main,
            bg="#181818",
            highlightthickness=1,
            highlightbackground="#555555",
            cursor="crosshair",
        )
        self.canvas.grid(row=2, column=0, sticky="nsew")

        ttk.Label(
            main,
            textvariable=self.status_var,
            wraplength=1000,
        ).grid(row=3, column=0, sticky="w", pady=(7, 0))

        # Make wheel / trackpad scrolling work over every widget in the left panel,
        # including labels, buttons and spinboxes, without affecting image zoom.
        self._bind_controls_wheel_recursive(self.controls_host)

        self._mode_changed()

    # -----------------------------------------------------------------
    # Events / display
    # -----------------------------------------------------------------

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.15))
        self.canvas.bind("<Configure>", lambda e: self._redraw())

    def _bind_controls_wheel_recursive(self, widget):
        """Bind wheel/trackpad scrolling to every widget in the left panel."""
        widget.bind("<MouseWheel>", self._controls_mousewheel, add="+")
        widget.bind("<Button-4>", self._controls_mousewheel, add="+")
        widget.bind("<Button-5>", self._controls_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_controls_wheel_recursive(child)

    def _controls_content_configure(self, event=None):
        """Update the vertical scroll range when control content changes size."""
        if hasattr(self, "controls_scroll_canvas"):
            bbox = self.controls_scroll_canvas.bbox("all")
            if bbox is not None:
                self.controls_scroll_canvas.configure(scrollregion=bbox)

    def _controls_canvas_configure(self, event):
        """Keep the embedded control frame matched to the visible panel width."""
        if hasattr(self, "controls_window"):
            self.controls_scroll_canvas.itemconfigure(
                self.controls_window,
                width=max(1, int(event.width)),
            )
        self._controls_content_configure()

    def _controls_mousewheel(self, event):
        """Scroll the left control panel only while the pointer is over it."""
        if not hasattr(self, "controls_scroll_canvas"):
            return None

        canvas = self.controls_scroll_canvas
        try:
            px, py = self.winfo_pointerxy()
            x0 = canvas.winfo_rootx()
            y0 = canvas.winfo_rooty()
            x1 = x0 + canvas.winfo_width()
            y1 = y0 + canvas.winfo_height()
        except tk.TclError:
            return None

        if not (x0 <= px < x1 and y0 <= py < y1):
            return None

        if getattr(event, "num", None) == 4:
            steps = -3
        elif getattr(event, "num", None) == 5:
            steps = 3
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            direction = -1 if delta > 0 else 1
            # Windows often reports ±120; macOS trackpads may report small
            # incremental values. This handles both without producing zero steps.
            magnitude = max(1, int(abs(delta) / 120))
            steps = direction * magnitude * 3

        canvas.yview_scroll(steps, "units")
        return "break"

    def _mode_changed(self):
        calibrate = self.operation_mode.get() == "calibrate"
        self.known_spin.configure(state="normal" if calibrate else "disabled")
        if hasattr(self, "reference_factor_spin"):
            self.reference_factor_spin.configure(state="disabled" if calibrate else "normal")
        self.load_scale_btn.configure(state="normal")
        self._reference_settings_changed()

    def _reference_settings_changed(self):
        try:
            k = float(self.reference_correction_factor_var.get())
            if not np.isfinite(k) or k <= 0:
                raise ValueError
            if self.operation_mode.get() == "calibrate":
                self.reference_info_var.set(
                    "Calibration mode: PROCESS computes K_ref = known width / raw homography median."
                )
            elif self.use_reference_correction.get():
                self.reference_info_var.set(f"K_ref: {k:.9f} | D_final = D_homography × K_ref")
            else:
                self.reference_info_var.set("Reference correction OFF | K_ref = 1.000000")
        except Exception:
            self.reference_info_var.set("Reference factor: enter a valid positive value")

    def _set_current_roi_as_fixed(self):
        if len(self.roi_points) != 4:
            messagebox.showwarning("Fixed zone", "Select four ROI points first (TL → TR → BR → BL).")
            return
        pts = [(float(x), float(y)) for x, y in self.roi_points]
        self.fixed_roi_points = pts
        self.use_fixed_roi.set(True)
        try:
            x0, y0, x1, y1 = self._roi_bounds()
            self.fixed_roi_info_var.set(
                f"Fixed zone: x={x0}…{x1}, y={y0}…{y1} | {x1-x0+1}×{y1-y0+1} px"
            )
        except Exception:
            self.fixed_roi_info_var.set("Fixed zone: 4 points stored")
        self.status_var.set(
            "Current ROI stored as the fixed Direct View measurement zone. "
            "Calibrate K_ref in this same zone, then save the Direct View Profile."
        )
        self._redraw()

    def _apply_fixed_roi_to_current(self, silent=False):
        if not self.fixed_roi_points or len(self.fixed_roi_points) != 4:
            if not silent:
                messagebox.showwarning("Fixed zone", "No fixed measurement zone has been stored or loaded.")
            return False
        if self.image_cv is not None:
            h, w = self.image_cv.shape[:2]
            for x, y in self.fixed_roi_points:
                if not (0 <= x < w and 0 <= y < h):
                    if not silent:
                        messagebox.showerror(
                            "Fixed zone outside image",
                            f"Stored fixed ROI does not fit the current {w}×{h} working image."
                        )
                    return False
        self.roi_points = [(float(x), float(y)) for x, y in self.fixed_roi_points]
        self.roi_info_var.set("4 / 4 ROI points selected (FIXED)")
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None
        self._invalidate_processing()
        self.view_mode.set("original")
        try:
            x0, y0, x1, y1 = self._roi_bounds()
            self.fixed_roi_info_var.set(
                f"Fixed zone: x={x0}…{x1}, y={y0}…{y1} | {x1-x0+1}×{y1-y0+1} px"
            )
        except Exception:
            pass
        if hasattr(self, "canvas"):
            self._redraw()
        if not silent:
            self.status_var.set("Fixed Direct View measurement zone reapplied.")
        return True

    def _fixed_roi_toggle_changed(self):
        if self.use_fixed_roi.get():
            if not self._apply_fixed_roi_to_current(silent=True):
                self.use_fixed_roi.set(False)
                self.status_var.set("No fixed zone available. Select ROI 3 and click 'Set Current ROI as Fixed Zone'.")
            else:
                self.status_var.set("Fixed measurement zone enabled.")
        else:
            self.status_var.set("Fixed measurement zone disabled; ROI can be edited manually.")

    def _clear_fixed_roi(self):
        self.fixed_roi_points = None
        self.use_fixed_roi.set(False)
        self.fixed_roi_info_var.set("Fixed zone: not set")
        self.status_var.set("Fixed measurement zone cleared. Current ROI remains available for editing.")

    def _build_direct_view_profile(self):
        if self.planar_calibration_data is None or self.homography_original_px_to_mm is None:
            raise ValueError("Load/import the local planar calibration first.")
        if not self.fixed_roi_points or len(self.fixed_roi_points) != 4:
            raise ValueError("Set the clean ROI 3 as the fixed measurement zone first.")
        k = float(self.reference_correction_factor_var.get())
        if not np.isfinite(k) or k <= 0:
            raise ValueError("K_ref must be a positive number.")
        pts = [[float(x), float(y)] for x, y in self.fixed_roi_points]
        a = np.asarray(pts, dtype=np.float64)
        x0 = int(math.floor(np.min(a[:, 0]))); x1 = int(math.ceil(np.max(a[:, 0])))
        y0 = int(math.floor(np.min(a[:, 1]))); y1 = int(math.ceil(np.max(a[:, 1])))
        return {
            "calibration_type": "direct_view_calibration_profile_v11",
            "profile_version": 11,
            "view_name": "Direct View",
            "working_rotation_degrees_clockwise": int(self.working_rotation_degrees_clockwise),
            "original_image_width_px": int(self.calibration_original_width_px),
            "original_image_height_px": int(self.calibration_original_height_px),
            "local_planar_calibration": self.planar_calibration_data,
            "target_plane_reference_correction": {
                "enabled": bool(self.use_reference_correction.get()),
                "correction_factor_K_ref": float(k),
                "record": self.reference_calibration_record,
                "relation": "D_final = D_homography * K_ref",
                "interpretation": "effective target-plane correction measured in the fixed working zone; not a physical camera-distance estimate",
            },
            "fixed_measurement_zone": {
                "coordinate_system": "90deg_clockwise_working_image_px",
                "roi_points_TL_TR_BR_BL": pts,
                "roi_bounds_xyxy": [x0, y0, x1, y1],
                "width_px": int(x1 - x0 + 1),
                "height_px": int(y1 - y0 + 1),
            },
            "algorithm_settings": {
                "gradient_multiplier_m": float(self.gradient_multiplier.get()),
                "gradient_threshold": float(self.gradient_threshold.get()),
                "roi_edge_margin_px": int(self.edge_margin_px.get()),
                "use_signed_gradient_polarity": bool(self.use_signed_gradient.get()),
                "use_50_percent_intensity_crossing": bool(self.use_intensity_crossing.get()),
                "use_subpixel_peak_localization_v6_comparison": bool(self.use_subpixel.get()),
                "use_static_defect_mask": bool(self.use_defect_mask.get()),
                "use_edge_continuity_tracking": bool(self.use_continuity.get()),
                "max_edge_jump_px": float(self.max_edge_jump_px.get()),
            },
            "workflow_note": "Use one clean fixed Direct View window (ROI 3). Calibrate K_ref with the precision reference in this same zone, then measure filament without moving the optical geometry.",
        }

    def _save_direct_view_profile(self):
        try:
            profile = self._build_direct_view_profile()
        except Exception as exc:
            messagebox.showerror("Cannot save profile", str(exc))
            return
        p = filedialog.asksaveasfilename(
            title="Save Direct View calibration profile",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="direct_view_calibration.json",
        )
        if not p:
            return
        Path(p).write_text(json.dumps(profile, indent=2), encoding="utf-8")
        self.direct_view_profile_path = Path(p)
        self.status_var.set(f"Saved unified Direct View profile: {Path(p).name}")

    def _load_direct_view_profile_dialog(self):
        p = filedialog.askopenfilename(
            title="Load Direct View calibration profile",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not p:
            return
        try:
            profile = json.loads(Path(p).read_text(encoding="utf-8"))
            if profile.get("calibration_type") != "direct_view_calibration_profile_v11":
                raise ValueError("JSON is not a direct_view_calibration_profile_v11 profile.")
            if int(profile.get("working_rotation_degrees_clockwise", -1)) != 90:
                raise ValueError("Direct View profile must use the fixed 90° CW working orientation.")

            data = profile["local_planar_calibration"]
            if data.get("calibration_type") != "local_planar_homography":
                raise ValueError("Embedded planar calibration is invalid.")
            rotation = int(data.get("rotation_degrees_clockwise_in_ui", -1))
            if rotation != self.working_rotation_degrees_clockwise:
                raise ValueError("Embedded planar calibration rotation does not match v11.")
            Horig = np.asarray(data["homography_original_camera_px_to_mm"], dtype=np.float64)
            Hwork = np.asarray(data["homography_calibration_view_px_to_mm"], dtype=np.float64)
            T = np.asarray(data["transform_original_px_to_calibration_view_px"], dtype=np.float64)
            if Horig.shape != (3, 3) or Hwork.shape != (3, 3) or T.shape != (3, 3):
                raise ValueError("Embedded homography matrices must all be 3×3.")
            composed = Hwork @ T; composed = composed / composed[2, 2]
            Horig_n = Horig / Horig[2, 2]
            if not np.allclose(composed, Horig_n, rtol=1e-7, atol=1e-8):
                raise ValueError("Embedded planar calibration transforms are internally inconsistent.")

            self.direct_view_profile_path = Path(p)
            self.planar_calibration_path = Path(p)
            self.planar_calibration_data = data
            self.homography_original_px_to_mm = Horig
            self.homography_working_px_to_mm = Hwork
            self.calibration_original_width_px = int(data["original_image_width_px"])
            self.calibration_original_height_px = int(data["original_image_height_px"])

            corr = profile.get("target_plane_reference_correction", {})
            k = float(corr.get("correction_factor_K_ref", 1.0))
            if not np.isfinite(k) or k <= 0:
                raise ValueError("Profile K_ref is invalid.")
            self.reference_correction_factor_var.set(k)
            self.reference_correction_factor = k
            self.use_reference_correction.set(bool(corr.get("enabled", True)))
            self.reference_calibration_record = corr.get("record") or {
                "calibration_type": "target_plane_reference_correction",
                "correction_factor": k,
            }

            zone = profile.get("fixed_measurement_zone", {})
            pts = zone.get("roi_points_TL_TR_BR_BL")
            if not isinstance(pts, list) or len(pts) != 4:
                raise ValueError("Profile does not contain a valid fixed measurement zone.")
            self.fixed_roi_points = [(float(q[0]), float(q[1])) for q in pts]
            self.use_fixed_roi.set(True)

            cfg = profile.get("algorithm_settings", {})
            if "gradient_multiplier_m" in cfg: self.gradient_multiplier.set(float(cfg["gradient_multiplier_m"]))
            if "gradient_threshold" in cfg: self.gradient_threshold.set(float(cfg["gradient_threshold"]))
            if "roi_edge_margin_px" in cfg: self.edge_margin_px.set(int(cfg["roi_edge_margin_px"]))
            if "use_signed_gradient_polarity" in cfg: self.use_signed_gradient.set(bool(cfg["use_signed_gradient_polarity"]))
            if "use_50_percent_intensity_crossing" in cfg: self.use_intensity_crossing.set(bool(cfg["use_50_percent_intensity_crossing"]))
            if "use_subpixel_peak_localization_v6_comparison" in cfg: self.use_subpixel.set(bool(cfg["use_subpixel_peak_localization_v6_comparison"]))
            if "use_static_defect_mask" in cfg: self.use_defect_mask.set(bool(cfg["use_static_defect_mask"]))
            if "use_edge_continuity_tracking" in cfg: self.use_continuity.set(bool(cfg["use_edge_continuity_tracking"]))
            if "max_edge_jump_px" in cfg: self.max_edge_jump_px.set(float(cfg["max_edge_jump_px"]))

            board_w = float(data.get("board_width_mm", float("nan")))
            board_h = float(data.get("board_height_mm", float("nan")))
            sx = float(data.get("center_px_per_mm_x", float("nan")))
            sy = float(data.get("center_px_per_mm_y", float("nan")))
            self.scale_info_var.set(
                f"PROFILE: {Path(p).name}\n"
                f"Board: {board_w:g} × {board_h:g} mm | rotation 90° CW\n"
                f"Center: {sx:.3f} px/mm X | {sy:.3f} px/mm Y | K_ref={k:.9f}"
            )
            x0, y0, x1, y1 = [int(v) for v in zone.get("roi_bounds_xyxy", [0,0,0,0])]
            self.fixed_roi_info_var.set(
                f"Fixed zone: x={x0}…{x1}, y={y0}…{y1} | {zone.get('width_px','?')}×{zone.get('height_px','?')} px"
            )
            self.operation_mode.set("measure")
            self._mode_changed()
            self._reference_settings_changed()
            if self.image_cv is not None:
                self._apply_fixed_roi_to_current(silent=True)
            self._invalidate_processing()
            self.status_var.set(f"Loaded unified Direct View profile: {Path(p).name}")
        except Exception as exc:
            messagebox.showerror("Profile load failed", str(exc))

    def _load_reference_correction_dialog(self):
        p = filedialog.askopenfilename(
            title="Load target-plane reference correction",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not p:
            return
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if data.get("calibration_type") != "target_plane_reference_correction":
                raise ValueError("Not a target_plane_reference_correction JSON.")
            k = float(data["correction_factor"])
            if not np.isfinite(k) or k <= 0:
                raise ValueError("Correction factor must be positive.")
            self.reference_correction_factor_var.set(k)
            self.reference_correction_factor = k
            self.reference_correction_path = Path(p)
            self.reference_calibration_record = data
            self._reference_settings_changed()
            self.status_var.set(f"Loaded K_ref: {Path(p).name} | {k:.9f}")
        except Exception as exc:
            messagebox.showerror("K_ref load failed", str(exc))

    def _save_reference_correction(self):
        try:
            k = float(self.reference_correction_factor_var.get())
            if not np.isfinite(k) or k <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror("Invalid K_ref", "K_ref must be positive.")
            return
        p = filedialog.asksaveasfilename(
            title="Save target-plane reference correction",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="direct_view_target_plane_reference_correction.json",
        )
        if not p:
            return
        record = dict(self.reference_calibration_record or {})
        record.update({
            "calibration_type": "target_plane_reference_correction",
            "correction_factor": k,
            "relation": "D_final = D_homography * correction_factor",
            "interpretation": "effective target-plane correction; not a physical camera-distance estimate",
            "planar_calibration_json": str(self.planar_calibration_path) if self.planar_calibration_path else None,
            "working_rotation_degrees_clockwise": 90,
        })
        Path(p).write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.reference_correction_path = Path(p)
        self.reference_calibration_record = record
        self.status_var.set(f"Saved K_ref: {Path(p).name}")

    def _depth_settings_changed(self):
        try:
            zf = float(self.filament_plane_distance_mm.get())
            dz = float(self.calibration_plane_closer_by_mm.get())
            zc = zf - dz
            if zf <= 0 or dz < 0 or zc <= 0:
                self.depth_info_var.set("Depth factor: invalid Z values")
                return
            k = zf / zc if self.use_depth_correction.get() else 1.0
            if self.use_depth_correction.get():
                self.depth_info_var.set(
                    f"Depth factor: {k:.6f}  | Zc={zc:.3f} mm, Zf={zf:.3f} mm"
                )
            else:
                self.depth_info_var.set("Depth correction: OFF (factor = 1.000000)")
        except Exception:
            self.depth_info_var.set("Depth factor: enter valid numeric values")

    def _view_changed(self):
        self._fit_view()

    def _update_cursor(self):
        self.canvas.configure(
            cursor="fleur" if self.tool_mode.get() == "pan" else "crosshair"
        )

    def _load_image_dialog(self):
        p = filedialog.askopenfilename(
            title="Choose full-resolution filament / rod image",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if p:
            self._load_image(Path(p))

    def _load_image(self, path):
        image_raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image_raw is None:
            messagebox.showerror("Open failed", f"Cannot read:\n{path}")
            return

        # IMPORTANT ORIENTATION CONVENTION (v5):
        # Rotate the complete camera frame 90° clockwise BEFORE display, ROI selection,
        # defect masking and diameter extraction. The filament therefore runs horizontally;
        # its original LEFT/RIGHT boundaries become TOP/BOTTOM boundaries in the working view.
        image = cv2.rotate(image_raw, cv2.ROTATE_90_CLOCKWISE)

        self.image_path = path
        self.image_cv_raw = image_raw
        self.image_cv = image
        self.image_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None

        if self.background_cv is not None and self.background_cv.shape[:2] != image.shape[:2]:
            self.background_path = None
            self.background_cv_raw = None
            self.background_cv = None
            self.background_info_var.set("No background reference loaded")

        h0, w0 = image_raw.shape[:2]
        h, w = image.shape[:2]
        self.image_info_var.set(
            f"{path.name}\n"
            f"Camera: {w0} × {h0} px | Working: {w} × {h} px (90° CW)"
        )

        self._reset_points()
        self._invalidate_processing()
        self.view_mode.set("original")
        if self.use_fixed_roi.get() and self.fixed_roi_points:
            applied = self._apply_fixed_roi_to_current(silent=True)
        else:
            applied = False
        self._fit_view()
        if applied:
            self.status_var.set(
                "Image loaded. Fixed Direct View measurement zone applied automatically; click PROCESS."
            )
        else:
            self.status_var.set(
                "Image loaded and rotated 90° clockwise. Select ROI 3 (TL → TR → BR → BL), "
                "then set it as the fixed measurement zone."
            )

    def _load_background_dialog(self):
        p = filedialog.askopenfilename(
            title="Choose background reference image (rod / filament removed)",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not p:
            return

        image_raw = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if image_raw is None:
            messagebox.showerror("Background load failed", f"Cannot read:\n{p}")
            return

        image = cv2.rotate(image_raw, cv2.ROTATE_90_CLOCKWISE)

        if self.image_cv is not None and image.shape[:2] != self.image_cv.shape[:2]:
            messagebox.showerror(
                "Image size mismatch",
                (
                    f"Rotated background is {image.shape[1]}×{image.shape[0]}, but the "
                    f"rotated measurement image is {self.image_cv.shape[1]}×{self.image_cv.shape[0]}.\n\n"
                    "Capture/load a background reference at the same full resolution."
                ),
            )
            return

        self.background_path = Path(p)
        self.background_cv_raw = image_raw
        self.background_cv = image
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None

        h0, w0 = image_raw.shape[:2]
        h, w = image.shape[:2]
        self.background_info_var.set(
            f"{self.background_path.name}\n"
            f"Camera: {w0} × {h0} px | Working: {w} × {h} px (90° CW) | rod removed"
        )
        self.status_var.set(
            "Background loaded and rotated 90° clockwise. Select the same working-view ROI, "
            "then Build Defect Mask."
        )


    def _build_defect_mask(self, silent=False):
        try:
            if self.background_cv is None:
                raise ValueError(
                    "Load a background reference image with the rod / filament removed first."
                )
            if self.image_cv is None:
                raise ValueError("Load the measurement image first.")
            if self.background_cv.shape[:2] != self.image_cv.shape[:2]:
                raise ValueError(
                    "Background reference and measurement image must have identical rotated dimensions."
                )

            x0, y0, x1, y1 = self._roi_bounds()
            bg_patch = self.background_cv[y0:y1+1, x0:x1+1]
            bg_gray = cv2.cvtColor(bg_patch, cv2.COLOR_BGR2GRAY)

            m = float(self.gradient_multiplier.get())
            defect_thr = float(self.defect_gradient_threshold.get())
            radius = int(max(0, self.defect_peak_radius_px.get()))

            # Filament is horizontal in v5. Each paper-style one-pixel slice is a
            # VERTICAL column, and the profile coordinate runs from TOP to BOTTOM.
            g_bg = np.zeros_like(bg_gray, dtype=np.float64)
            if bg_gray.shape[0] >= 3:
                f = bg_gray.astype(np.float64)
                g_bg[1:-1, :] = m * np.abs(f[2:, :] - f[:-2, :])

            defect = np.zeros_like(bg_gray, dtype=np.uint8)
            defect_peak_count = 0

            # Find local gradient maxima independently in every column. Around each
            # static peak, mask only a small VERTICAL neighbourhood ±radius pixels.
            for x in range(bg_gray.shape[1]):
                peaks = local_maxima_above(g_bg[:, x], defect_thr)
                defect_peak_count += int(len(peaks))

                for y in peaks:
                    ya = max(0, int(y) - radius)
                    yb = min(bg_gray.shape[0], int(y) + radius + 1)
                    defect[ya:yb, x] = 255

            self.defect_mask_roi = defect
            self.defect_background_gray = bg_gray
            self.defect_mask_coverage = float(np.mean(defect > 0) * 100.0)

            self.status_var.set(
                f"Defect mask built from {defect_peak_count} vertical-slice background peaks. "
                f"Masked ROI area: {self.defect_mask_coverage:.2f}%. "
                "Open 'Defect Mask' view to inspect it."
            )
            self.view_mode.set("defect")
            self._fit_view()

            if self.defect_mask_coverage > 35.0 and not silent:
                messagebox.showwarning(
                    "Large defect mask",
                    (
                        f"The mask covers {self.defect_mask_coverage:.1f}% of the ROI. "
                        "The defect threshold may be too low or the peak radius too large."
                    ),
                )
            return True

        except Exception as exc:
            if silent:
                return False
            messagebox.showerror("Build defect mask failed", str(exc))
            return False

    def _load_scale_dialog(self):
        p = filedialog.askopenfilename(
            title="Load local planar calibration",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not p:
            return
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if data.get("calibration_type") != "local_planar_homography":
                raise ValueError("JSON is not a local_planar_homography calibration.")

            rotation = int(data.get("rotation_degrees_clockwise_in_ui", -1))
            if rotation != self.working_rotation_degrees_clockwise:
                raise ValueError(
                    f"Calibration rotation is {rotation}°, but diameter v8 uses "
                    f"{self.working_rotation_degrees_clockwise}° CW."
                )

            Horig = np.asarray(data["homography_original_camera_px_to_mm"], dtype=np.float64)
            Hwork = np.asarray(data["homography_calibration_view_px_to_mm"], dtype=np.float64)
            T = np.asarray(data["transform_original_px_to_calibration_view_px"], dtype=np.float64)
            if Horig.shape != (3, 3) or Hwork.shape != (3, 3) or T.shape != (3, 3):
                raise ValueError("Calibration homography matrices must all be 3x3.")

            # Internal consistency check: H_original should equal H_working * T
            # up to projective scale.  This catches mismatched/edited JSON files.
            composed = Hwork @ T
            composed = composed / composed[2, 2]
            Horig_n = Horig / Horig[2, 2]
            if not np.allclose(composed, Horig_n, rtol=1e-7, atol=1e-8):
                raise ValueError("Calibration coordinate transforms are internally inconsistent.")

            self.planar_calibration_path = Path(p)
            self.planar_calibration_data = data
            self.homography_original_px_to_mm = Horig
            self.homography_working_px_to_mm = Hwork
            self.calibration_original_width_px = int(data["original_image_width_px"])
            self.calibration_original_height_px = int(data["original_image_height_px"])

            board_w = float(data.get("board_width_mm", float("nan")))
            board_h = float(data.get("board_height_mm", float("nan")))
            sx = float(data.get("center_px_per_mm_x", float("nan")))
            sy = float(data.get("center_px_per_mm_y", float("nan")))
            self.scale_info_var.set(
                f"{Path(p).name}\n"
                f"Board: {board_w:g} × {board_h:g} mm | rotation 90° CW\n"
                f"Center: {sx:.3f} px/mm X | {sy:.3f} px/mm Y"
            )
            self._invalidate_processing()
        except Exception as exc:
            self.planar_calibration_path = None
            self.planar_calibration_data = None
            self.homography_original_px_to_mm = None
            self.homography_working_px_to_mm = None
            messagebox.showerror("Planar calibration load failed", str(exc))

    def _invalidate_processing(self):
        self.rectified_gray = None
        self.segment_mask = None
        self.rectified_mask = None
        self.top_edges = None
        self.bottom_edges = None
        self.centers = None
        self.valid = None
        self.widths_px = None
        self.diameters_mm = None
        self.diameters_mm_homography_raw = None
        self.depth_correction_factor = 1.0
        self.top_points_working_px = None
        self.bottom_points_working_px = None
        self.top_points_original_px = None
        self.bottom_points_original_px = None
        self.top_points_mm = None
        self.bottom_points_mm = None
        self.sample_profiles = {}
        self.raw_peaks_by_col = {}
        self.kept_peaks_by_col = {}
        self.rejected_peaks_by_col = {}
        self.signed_gradients_by_col = {}
        self.top_candidate_peaks_by_col = {}
        self.bottom_candidate_peaks_by_col = {}
        self.intensity_crossing_by_col = {}
        self.result_var.set("Processing: not calculated")

    def _canvas_to_image(self, cx, cy):
        if self.image_cv is None or self.display_scale <= 0:
            return None
        x = (cx - self.display_offset_x) / self.display_scale
        y = (cy - self.display_offset_y) / self.display_scale
        h, w = self.image_cv.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return None
        return float(x), float(y)

    def _canvas_press(self, event):
        if self.image_cv is None:
            return

        if self.tool_mode.get() == "pan":
            self._drag_start = (event.x, event.y)
            self._drag_pan_start = (self.pan_x, self.pan_y)
            return

        if self.view_mode.get() != "original":
            return

        if self.use_fixed_roi.get():
            self.status_var.set("Fixed measurement zone is enabled. Disable it before editing ROI points.")
            return

        if len(self.roi_points) >= 4:
            return

        p = self._canvas_to_image(event.x, event.y)
        if p is None:
            return

        self.roi_points.append(p)
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None
        self._invalidate_processing()
        self.view_mode.set("original")

        names = ["TL", "TR", "BR", "BL"]
        self.roi_info_var.set(f"{len(self.roi_points)} / 4 ROI points selected")

        if len(self.roi_points) < 4:
            self.status_var.set(
                f"Point {len(self.roi_points)} = {names[len(self.roi_points)-1]}. "
                f"Now click {names[len(self.roi_points)]}."
            )
        else:
            self.status_var.set(
                "Four ROI points selected in the 90° CW working view. Click PROCESS USING PAPER METHOD."
            )
        self._redraw()

    def _canvas_drag(self, event):
        if self.image_cv is None or self.tool_mode.get() != "pan":
            return
        if self._drag_start is None or self._drag_pan_start is None:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self.pan_x = self._drag_pan_start[0] + dx
        self.pan_y = self._drag_pan_start[1] + dy
        self._redraw()

    def _canvas_release(self, event):
        self._drag_start = None
        self._drag_pan_start = None

    def _undo_point(self):
        if self.use_fixed_roi.get():
            self.status_var.set("Fixed measurement zone is enabled. Disable it before editing ROI points.")
            return
        if self.roi_points:
            self.roi_points.pop()
            self.defect_mask_roi = None
            self.defect_background_gray = None
            self.defect_mask_coverage = None
            self.roi_info_var.set(f"{len(self.roi_points)} / 4 ROI points selected")
            self._invalidate_processing()
            self.view_mode.set("original")
            self.status_var.set("Click ROI corners: TL → TR → BR → BL.")
            self._redraw()

    def _reset_points(self):
        self.roi_points.clear()
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None
        self.roi_info_var.set("0 / 4 ROI points selected")
        self._invalidate_processing()
        if hasattr(self, "view_mode"):
            self.view_mode.set("original")
        if hasattr(self, "canvas"):
            self._redraw()

    def _fit_view(self):
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._redraw()

    def _actual_size_view(self):
        pil = self._current_display_pil()
        if pil is None:
            return
        cw = max(self.canvas.winfo_width(), 300)
        ch = max(self.canvas.winfo_height(), 250)
        iw, ih = pil.size
        fit_scale = min(cw / iw, ch / ih)
        if fit_scale <= 0:
            return
        self.zoom_factor = max(0.05, min(50.0, 1.0 / fit_scale))
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._redraw()

    def _zoom_step(self, factor):
        pil = self._current_display_pil()
        if pil is None:
            return
        cx = self.canvas.winfo_width() / 2.0
        cy = self.canvas.winfo_height() / 2.0
        self._zoom_at(cx, cy, factor)

    def _mouse_wheel(self, event):
        if self._current_display_pil() is None:
            return
        if event.delta > 0:
            factor = 1.15
        elif event.delta < 0:
            factor = 1 / 1.15
        else:
            return
        self._zoom_at(event.x, event.y, factor)

    def _zoom_at(self, canvas_x, canvas_y, factor):
        pil = self._current_display_pil()
        if pil is None:
            return

        cw = max(self.canvas.winfo_width(), 300)
        ch = max(self.canvas.winfo_height(), 250)
        iw, ih = pil.size

        fit_scale = min(cw / iw, ch / ih)
        old_scale = fit_scale * self.zoom_factor
        if old_scale <= 0:
            return

        old_dw = iw * old_scale
        old_dh = ih * old_scale
        old_center_x = (cw - old_dw) / 2.0
        old_center_y = (ch - old_dh) / 2.0
        old_offset_x = old_center_x + self.pan_x
        old_offset_y = old_center_y + self.pan_y

        sx = (canvas_x - old_offset_x) / old_scale
        sy = (canvas_y - old_offset_y) / old_scale

        self.zoom_factor = float(np.clip(self.zoom_factor * factor, 0.10, 40.0))

        new_scale = fit_scale * self.zoom_factor
        new_dw = iw * new_scale
        new_dh = ih * new_scale
        new_center_x = (cw - new_dw) / 2.0
        new_center_y = (ch - new_dh) / 2.0

        desired_ox = canvas_x - sx * new_scale
        desired_oy = canvas_y - sy * new_scale

        self.pan_x = desired_ox - new_center_x
        self.pan_y = desired_oy - new_center_y
        self._redraw()

    def _current_display_pil(self):
        mode = self.view_mode.get()

        if mode == "original":
            if self.image_pil is None:
                return None
            display = self.image_pil.copy()
            draw = ImageDraw.Draw(display)

            if self.roi_points:
                colors = ["#ff3030", "#ff9500", "#20c65a", "#25a7ff"]
                labels = ["1 TL", "2 TR", "3 BR", "4 BL"]
                r = max(7, int(display.width / 550))
                for i, (x, y) in enumerate(self.roi_points):
                    draw.ellipse(
                        [x-r, y-r, x+r, y+r],
                        fill=colors[i],
                        outline="white",
                        width=max(2, r // 3),
                    )
                    draw.text((x+r+4, y-r), labels[i], fill=colors[i])

                if len(self.roi_points) == 4:
                    arr = np.asarray(self.roi_points, dtype=np.float64)
                    x0 = float(np.min(arr[:, 0]))
                    x1 = float(np.max(arr[:, 0]))
                    y0 = float(np.min(arr[:, 1]))
                    y1 = float(np.max(arr[:, 1]))
                    draw.rectangle(
                        [x0, y0, x1, y1],
                        outline="#00ffff",
                        width=max(3, int(display.width / 1200)),
                    )
                elif len(self.roi_points) >= 2:
                    draw.line(
                        list(self.roi_points),
                        fill="#00ffff",
                        width=max(3, int(display.width / 1200)),
                    )
            return display

        if mode == "defect":
            if self.defect_background_gray is None or self.defect_mask_roi is None:
                return None

            rgb = cv2.cvtColor(self.defect_background_gray, cv2.COLOR_GRAY2RGB)
            mask = self.defect_mask_roi > 0
            rgb[mask] = np.array([255, 0, 255], dtype=np.uint8)
            return Image.fromarray(rgb)

        if mode == "segmented":
            if self.segment_mask is None:
                return None
            rgb = cv2.cvtColor(self.segment_mask, cv2.COLOR_GRAY2RGB)
            pil = Image.fromarray(rgb)
            draw = ImageDraw.Draw(pil)

            if self.top_edges is not None and self.bottom_edges is not None:
                # In v5 columns are slices; boundary coordinates are y positions.
                for x in range(len(self.top_edges)):
                    if self.valid[x]:
                        yT = float(self.top_edges[x])
                        yB = float(self.bottom_edges[x])
                        draw.point((x, yT), fill="#ff3030")
                        draw.point((x, yB), fill="#ff3030")
                if self.centers is not None:
                    pts = [
                        (float(x), float(self.centers[x]))
                        for x in range(len(self.centers))
                        if self.valid[x]
                    ]
                    if len(pts) >= 2:
                        draw.line(pts, fill="#ff0000", width=2)
            return pil

        if mode == "rectified":
            if self.rectified_mask is None:
                return None
            rgb = cv2.cvtColor(self.rectified_mask, cv2.COLOR_GRAY2RGB)
            pil = Image.fromarray(rgb)
            draw = ImageDraw.Draw(pil)
            y = pil.height / 2.0
            draw.line([(0, y), (pil.width-1, y)], fill="#00ff66", width=2)
            return pil

        return None

    def _redraw(self):
        """Redraw only the visible viewport.

        The previous implementation resized the *entire* 2592×4608 working image
        at the current zoom. At high zoom (for example 1490%), that can create a
        temporary bitmap well above 100 million pixels and ImageTk may raise
        MemoryError. This version keeps the same image-coordinate transform, but
        crops the source to the part that is actually visible on the canvas before
        resizing. ROI clicking/panning therefore remains geometrically identical.
        """
        if not hasattr(self, "canvas"):
            return

        self.canvas.delete("all")
        pil = self._current_display_pil()

        if pil is None:
            self.canvas.create_text(
                max(self.canvas.winfo_width() / 2, 200),
                max(self.canvas.winfo_height() / 2, 150),
                text="Load image / process ROI",
                fill="white",
                font=("Helvetica", 18),
            )
            return

        cw = max(self.canvas.winfo_width(), 300)
        ch = max(self.canvas.winfo_height(), 250)
        iw, ih = pil.size
        fit_scale = min(cw / iw, ch / ih)
        scale = max(1e-9, fit_scale * self.zoom_factor)

        # Position of the FULL scaled image in canvas coordinates.  These values
        # are retained for _canvas_to_image(), so ROI point coordinates do not
        # change compared with the old renderer.
        full_dw = iw * scale
        full_dh = ih * scale
        ox = (cw - full_dw) / 2.0 + self.pan_x
        oy = (ch - full_dh) / 2.0 + self.pan_y

        self.display_scale = scale
        self.display_offset_x = ox
        self.display_offset_y = oy

        # Convert the visible canvas rectangle back to source-image coordinates.
        # Add a tiny source-pixel margin so antialiased edges do not disappear at
        # viewport boundaries during panning.
        margin_src = 2.0
        sx0 = max(0.0, (0.0 - ox) / scale - margin_src)
        sy0 = max(0.0, (0.0 - oy) / scale - margin_src)
        sx1 = min(float(iw), (float(cw) - ox) / scale + margin_src)
        sy1 = min(float(ih), (float(ch) - oy) / scale + margin_src)

        if sx1 <= sx0 or sy1 <= sy0:
            # Image is completely panned outside the canvas. Keep the coordinate
            # transform valid; there is simply nothing visible to render.
            self.preview_photo = None
            self.zoom_label_var.set(f"Zoom: {self.zoom_factor * 100:.0f}%")
            self._update_cursor()
            return

        # Integer crop bounds in source pixels.
        cx0 = max(0, int(math.floor(sx0)))
        cy0 = max(0, int(math.floor(sy0)))
        cx1 = min(iw, int(math.ceil(sx1)))
        cy1 = min(ih, int(math.ceil(sy1)))

        crop = pil.crop((cx0, cy0, cx1, cy1))

        # Resize only the visible crop. Its output stays approximately canvas-sized
        # regardless of zoom, preventing ImageTk/Pillow memory spikes.
        rdw = max(1, int(round((cx1 - cx0) * scale)))
        rdh = max(1, int(round((cy1 - cy0) * scale)))
        resized = crop.resize((rdw, rdh), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(resized)

        draw_x = ox + cx0 * scale
        draw_y = oy + cy0 * scale
        self.canvas.create_image(draw_x, draw_y, anchor="nw", image=self.preview_photo)

        self.zoom_label_var.set(f"Zoom: {self.zoom_factor * 100:.0f}%")
        self._update_cursor()

    # -----------------------------------------------------------------
    # Paper processing
    # -----------------------------------------------------------------

    def _process(self):
        if self.image_cv is None:
            messagebox.showwarning("No image", "Load a full-resolution image first.")
            return

        if len(self.roi_points) != 4:
            messagebox.showwarning(
                "Four ROI points required",
                "Click TL → TR → BR → BL around one filament projection."
            )
            return

        if self.homography_original_px_to_mm is None or self.planar_calibration_data is None:
            messagebox.showwarning(
                "No planar calibration",
                "Load a Direct View calibration profile, or import local_planar_calibration_direct.json during first-time setup."
            )
            return

        # The calibration and measurement must use the same full-resolution camera frame.
        raw_h, raw_w = self.image_cv_raw.shape[:2]
        if (raw_w != self.calibration_original_width_px or
                raw_h != self.calibration_original_height_px):
            messagebox.showerror(
                "Image size mismatch",
                f"Calibration expects {self.calibration_original_width_px} × "
                f"{self.calibration_original_height_px} px, but this image is "
                f"{raw_w} × {raw_h} px. Use the same full-resolution camera mode."
            )
            return

        if self.operation_mode.get() == "calibrate":
            try:
                d = float(self.known_diameter_mm.get())
                if d <= 0:
                    raise ValueError
            except Exception:
                messagebox.showerror("Invalid reference width", "Reference width must be > 0.")
                return

        # v10: no guessed Z distance. Measurement mode applies stored K_ref;
        # calibration mode derives K_ref after raw homography measurement.
        if self.operation_mode.get() == "measure":
            try:
                reference_factor = float(self.reference_correction_factor_var.get())
                if not np.isfinite(reference_factor) or reference_factor <= 0:
                    raise ValueError
                if not self.use_reference_correction.get():
                    reference_factor = 1.0
            except Exception:
                messagebox.showerror("Invalid K_ref", "K_ref must be a positive number.")
                return
        else:
            reference_factor = None

        if self.use_defect_mask.get():
            if self.background_cv is None:
                messagebox.showwarning(
                    "Background reference required",
                    (
                        "Static defect masking is enabled. Load a background reference "
                        "captured with the rod / filament removed, or disable the mask."
                    ),
                )
                return

            if self.defect_mask_roi is None:
                if not self._build_defect_mask(silent=True):
                    messagebox.showerror(
                        "Defect mask unavailable",
                        "Could not build a valid defect mask for the current ROI."
                    )
                    return

        try:
            self._run_paper_method(reference_factor=reference_factor)
        except Exception as exc:
            messagebox.showerror("Processing failed", str(exc))


    def _show_slice_diagnostic(self):
        if self.rectified_gray is None or self.valid is None:
            messagebox.showwarning("No result", "Run PROCESS USING PAPER METHOD first.")
            return

        valid_cols = np.flatnonzero(self.valid)
        if len(valid_cols) == 0:
            return
        target = self.rectified_gray.shape[1] // 2
        x = int(valid_cols[np.argmin(np.abs(valid_cols - target))])

        f = self.rectified_gray[:, x].astype(np.float64)
        m = float(self.gradient_multiplier.get())
        threshold = float(self.gradient_threshold.get())
        signed_g = np.zeros_like(f)
        signed_g[1:-1] = m * (f[2:] - f[:-2])
        abs_g = np.abs(signed_g)
        signed_mode = bool(self.use_signed_gradient.get())

        top_candidates = self.top_candidate_peaks_by_col.get(x, np.empty(0, dtype=np.int32))
        bottom_candidates = self.bottom_candidate_peaks_by_col.get(x, np.empty(0, dtype=np.int32))
        rejected_peaks = self.rejected_peaks_by_col.get(x, np.empty(0, dtype=np.int32))
        kept_peaks = self.kept_peaks_by_col.get(x, np.empty(0, dtype=np.int32))

        yT = float(self.top_edges[x])
        yB = float(self.bottom_edges[x])
        crossing_info = self.intensity_crossing_by_col.get(x)

        win = tk.Toplevel(self)
        win.title(f"Slice Diagnostic - column {x}")
        win.geometry("1080x650")
        mode_text = (
            "SIGNED gradient: Top=positive, Bottom=negative"
            if signed_mode else "PAPER baseline: absolute gradient extremes"
        )
        ttk.Label(
            win,
            text=(
                f"Vertical single-pixel slice | column={x} | TOP→BOTTOM | {mode_text} | "
                f"m={m:g} | threshold={threshold:g} | "
                f"localization={'50% crossing' if self.use_intensity_crossing.get() else 'gradient peak'}"
            ),
            font=("Helvetica", 12, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 6))

        canvas = tk.Canvas(win, bg="white", highlightthickness=1, highlightbackground="#777")
        canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        def draw_chart(event=None):
            canvas.delete("all")
            W = max(canvas.winfo_width(), 760)
            H = max(canvas.winfo_height(), 460)
            ml, mr, mt, mb = 65, 25, 30, 58
            pw, ph = W-ml-mr, H-mt-mb

            if signed_mode:
                plot_g = signed_g
                vmin = min(float(np.min(plot_g))*1.08, -threshold*1.20, 0.0)
                vmax = max(255.0, float(np.max(plot_g))*1.08, threshold*1.20)
            else:
                plot_g = abs_g
                vmin = 0.0
                vmax = max(255.0, float(np.max(plot_g))*1.08, threshold*1.20)
            if vmax <= vmin:
                vmax = vmin + 1.0

            def X(i):
                return ml + (float(i)/max(1, len(f)-1))*pw
            def Y(v):
                return mt + ph - ((float(v)-vmin)/(vmax-vmin))*ph

            canvas.create_line(ml, mt, ml, mt+ph, fill="black")
            canvas.create_line(ml, mt+ph, ml+pw, mt+ph, fill="black")

            if signed_mode:
                yz = Y(0)
                canvas.create_line(ml, yz, ml+pw, yz, fill="#777", dash=(3,3))
                yp = Y(threshold)
                yn = Y(-threshold)
                canvas.create_line(ml, yp, ml+pw, yp, fill="#16a34a", width=2)
                canvas.create_line(ml, yn, ml+pw, yn, fill="#0891b2", width=2)
                canvas.create_text(ml+5, yp-10, anchor="w", text="+ threshold (Top)", fill="#16a34a")
                canvas.create_text(ml+5, yn+11, anchor="w", text="- threshold (Bottom)", fill="#0891b2")
            else:
                yt = Y(threshold)
                canvas.create_line(ml, yt, ml+pw, yt, fill="#16a34a", width=2)
                canvas.create_text(ml+5, yt-10, anchor="w", text="Gradient threshold", fill="#16a34a")

            pts_f, pts_g = [], []
            for i in range(len(f)):
                pts_f.extend([X(i), Y(f[i])])
                pts_g.extend([X(i), Y(plot_g[i])])
            if len(pts_f) >= 4:
                canvas.create_line(*pts_f, fill="#2563eb", width=2)
                canvas.create_line(*pts_g, fill="#f97316", width=2)

            if signed_mode:
                for i in top_candidates:
                    xx, yy = X(i), Y(signed_g[i]); r=4
                    canvas.create_line(xx-r, yy-r, xx+r, yy+r, fill="#16a34a", width=2)
                    canvas.create_line(xx-r, yy+r, xx+r, yy-r, fill="#16a34a", width=2)
                for i in bottom_candidates:
                    xx, yy = X(i), Y(signed_g[i]); r=4
                    canvas.create_line(xx-r, yy-r, xx+r, yy+r, fill="#0891b2", width=2)
                    canvas.create_line(xx-r, yy+r, xx+r, yy-r, fill="#0891b2", width=2)
            else:
                for i in kept_peaks:
                    xx, yy = X(i), Y(abs_g[i]); r=4
                    canvas.create_line(xx-r, yy-r, xx+r, yy+r, fill="#16a34a", width=2)
                    canvas.create_line(xx-r, yy+r, xx+r, yy-r, fill="#16a34a", width=2)

            for i in rejected_peaks:
                val = signed_g[i] if signed_mode else abs_g[i]
                xx, yy = X(i), Y(val); r=5
                canvas.create_line(xx-r, yy-r, xx+r, yy+r, fill="#d946ef", width=2)
                canvas.create_line(xx-r, yy+r, xx+r, yy-r, fill="#d946ef", width=2)

            if crossing_info is not None:
                for pos, level, label, fill in (
                    (yT, crossing_info["i_edge_top"], "Top 50%", "#16a34a"),
                    (yB, crossing_info["i_edge_bottom"], "Bottom 50%", "#0891b2"),
                ):
                    xx, yy = X(pos), Y(level)
                    r = 5
                    canvas.create_oval(xx-r, yy-r, xx+r, yy+r, outline=fill, width=2)
                    canvas.create_text(xx+7, yy-8, anchor="w", text=f"{label}: I={level:.1f}", fill=fill)

            for pos, label in ((yT, "Top"), (yB, "Bottom")):
                xx = X(pos)
                canvas.create_line(xx, mt, xx, mt+ph, fill="#111111", dash=(5,4), width=2)
                canvas.create_text(xx+4, mt+36 if label=="Top" else mt+52, anchor="w", text=f"{label}: {pos:.3f}px")

            diag_text = f"Filament: {yB-yT:.4f} px"
            if self.diameters_mm is not None and np.isfinite(self.diameters_mm[x]):
                diag_text += f" | final {self.diameters_mm[x]:.5f} mm"
            canvas.create_text(
                (X(yT)+X(yB))/2, mt+18,
                text=diag_text,
                fill="black", font=("Helvetica", 12, "bold")
            )
            canvas.create_text(ml+pw/2, H-20, text="Vertical profile coordinate: Top → Bottom (px)")
            canvas.create_text(14, mt+ph/2, text="Intensity / gradient", angle=90)
            canvas.create_text(ml+10, mt+10, anchor="w", text="Blue: grayscale f(p)", fill="#2563eb")
            canvas.create_text(ml+175, mt+10, anchor="w", text="Orange: signed g(p)" if signed_mode else "Orange: |g(p)|", fill="#f97316")
            if signed_mode:
                canvas.create_text(ml+350, mt+10, anchor="w", text="Green ×: Top candidates", fill="#16a34a")
                canvas.create_text(ml+535, mt+10, anchor="w", text="Cyan ×: Bottom candidates", fill="#0891b2")
            else:
                canvas.create_text(ml+350, mt+10, anchor="w", text="Green ×: kept peaks", fill="#16a34a")

        canvas.bind("<Configure>", draw_chart)
        win.after(80, draw_chart)

    def _save_scale(self):
        if self.diameters_mm is None or self.widths_px is None or self.valid is None:
            messagebox.showwarning("No result", "Process an image first.")
            return
        if self.planar_calibration_path is None:
            messagebox.showwarning("No calibration", "Load a local planar calibration first.")
            return

        p = filedialog.asksaveasfilename(
            title="Save v11 measurement summary",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="diameter_v11_fixed_zone_summary.json",
        )
        if not p:
            return

        px_stats = robust_summary(self.widths_px[self.valid])
        mm_stats = robust_summary(self.diameters_mm[self.valid])
        data = {
            "measurement_type": "paper_gradient_v11_fixed_zone_planar_homography_reference_corrected",
            "reference": {
                "title": "Open Source Filament Diameter Sensor for Recycling, Winding and Additive Manufacturing Machines",
                "authors": "Aliaksei L. Petsiuk; Joshua M. Pearce",
                "year": 2021,
            },
            "source_image": str(self.image_path) if self.image_path else None,
            "direct_view_profile_json": str(self.direct_view_profile_path) if self.direct_view_profile_path else None,
            "fixed_measurement_zone_enabled": bool(self.use_fixed_roi.get()),
            "fixed_measurement_zone_points_working_px": (
                [[float(x), float(y)] for x, y in self.fixed_roi_points]
                if self.fixed_roi_points else None
            ),
            "working_rotation_degrees_clockwise": 90,
            "planar_calibration_json": str(self.planar_calibration_path),
            "planar_calibration_type": self.planar_calibration_data.get("calibration_type"),
            "physical_diameter_relation": "D_final = D_homography * K_ref",
            "target_plane_reference_correction": {
                "enabled": bool(self.use_reference_correction.get() or self.operation_mode.get() == "calibrate"),
                "correction_factor_K_ref": float(self.reference_correction_factor),
                "correction_json": str(self.reference_correction_path) if self.reference_correction_path else None,
                "record": self.reference_calibration_record,
                "interpretation": "effective target-plane correction; not a physical camera-distance estimate",
            },
            "coordinate_chain": "ROI-local 90CW working px -> full working px -> original camera px -> mm",
            "edge_localization": (
                "signed-gradient anchors + 50% intensity crossing"
                if self.use_intensity_crossing.get()
                else "strongest gradient peak comparison mode"
            ),
            "gradient_multiplier_m": float(self.gradient_multiplier.get()),
            "gradient_threshold": float(self.gradient_threshold.get()),
            "roi_clicks_working_rotated_px_TL_TR_BR_BL": [
                [float(x), float(y)] for x, y in self.roi_points
            ],
            "valid_single_pixel_slices": int(np.sum(self.valid)),
            "pixel_diameter_stats": px_stats,
            "diameter_mm_homography_raw_stats": robust_summary(self.diameters_mm_homography_raw[self.valid]),
            "diameter_mm_final_stats": mm_stats,
            "top_edge_position_roi_px": robust_summary(self.top_edges[self.valid]),
            "bottom_edge_position_roi_px": robust_summary(self.bottom_edges[self.valid]),
        }
        if self.operation_mode.get() == "calibrate":
            data["reference_calibration"] = self.reference_calibration_record

        Path(p).write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved v11 measurement summary: {Path(p).name}")

    def _save_csv(self):
        if self.widths_px is None or self.valid is None:
            messagebox.showwarning("No result", "Process an image first.")
            return

        p = filedialog.asksaveasfilename(
            title="Save single-pixel diameter measurements",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="paper_method_diameters.csv",
        )
        if not p:
            return

        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "slice_column_x_roi",
                "valid",
                "top_edge_y_roi_px",
                "bottom_edge_y_roi_px",
                "centerline_y_roi_px",
                "diameter_px",
                "working_x_px",
                "top_working_y_px",
                "bottom_working_y_px",
                "top_original_x_px",
                "top_original_y_px",
                "bottom_original_x_px",
                "bottom_original_y_px",
                "top_X_mm",
                "top_Y_mm",
                "bottom_X_mm",
                "bottom_Y_mm",
                "diameter_mm_homography_raw",
                "reference_correction_factor_K_ref",
                "diameter_mm_final",
            ])
            for x in range(len(self.widths_px)):
                ok = bool(self.valid[x])
                writer.writerow([
                    x,
                    ok,
                    None if not ok else float(self.top_edges[x]),
                    None if not ok else float(self.bottom_edges[x]),
                    None if not ok else float(self.centers[x]),
                    None if not ok else float(self.widths_px[x]),
                    None if not ok else float(self.top_points_working_px[x, 0]),
                    None if not ok else float(self.top_points_working_px[x, 1]),
                    None if not ok else float(self.bottom_points_working_px[x, 1]),
                    None if not ok else float(self.top_points_original_px[x, 0]),
                    None if not ok else float(self.top_points_original_px[x, 1]),
                    None if not ok else float(self.bottom_points_original_px[x, 0]),
                    None if not ok else float(self.bottom_points_original_px[x, 1]),
                    None if not ok else float(self.top_points_mm[x, 0]),
                    None if not ok else float(self.top_points_mm[x, 1]),
                    None if not ok else float(self.bottom_points_mm[x, 0]),
                    None if not ok else float(self.bottom_points_mm[x, 1]),
                    None if not ok else float(self.diameters_mm_homography_raw[x]),
                    None if not ok else float(self.reference_correction_factor),
                    None if not ok else float(self.diameters_mm[x]),
                ])

        self.status_var.set(f"Saved measurements: {Path(p).name}")

if __name__ == "__main__":
    PaperMethodDiameterUI().mainloop()

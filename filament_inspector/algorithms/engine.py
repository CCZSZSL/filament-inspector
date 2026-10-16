"""Original measurement engine, independent of Tk.

The caller supplies image/settings state and display hooks; the headless
view adapter supplies no-op hooks and plain get/set values.
"""
import math
import cv2
import numpy as np
from .edges import order_quad_tl_tr_br_bl, quad_output_size, local_maxima_above, parabolic_subpixel_peak, linear_intensity_crossing, fifty_percent_edge_pair, robust_summary, apply_homography_points

class PaperMethodEngine:
    def _roi_bounds(self):
        if len(self.roi_points) != 4:
            raise ValueError("Four ROI points are required.")

        pts = np.asarray(self.roi_points, dtype=np.float64)
        if self.image_cv is None:
            raise ValueError("Measurement image is not loaded.")

        h_img, w_img = self.image_cv.shape[:2]
        x0 = int(max(0, math.floor(float(np.min(pts[:, 0])))))
        x1 = int(min(w_img - 1, math.ceil(float(np.max(pts[:, 0])))))
        y0 = int(max(0, math.floor(float(np.min(pts[:, 1])))))
        y1 = int(min(h_img - 1, math.ceil(float(np.max(pts[:, 1])))))

        if x1 - x0 < 12 or y1 - y0 < 12:
            raise ValueError("ROI is too small.")

        # v5 has one fixed orientation only. No automatic ROI rotation is allowed.
        # The full camera frame was already rotated 90° clockwise at load time.
        return x0, y0, x1, y1

    def _run_paper_method(self, reference_factor=None):
        # -------------------------------------------------------------
        # Fixed orientation:
        #   full frame is rotated 90° clockwise at load time;
        #   filament runs left-to-right;
        #   each x-column is one single-pixel diameter slice;
        #   gradient profile runs vertically TOP -> BOTTOM.
        # -------------------------------------------------------------
        x0, y0, x1, y1 = self._roi_bounds()

        patch = self.image_cv[y0:y1 + 1, x0:x1 + 1]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)

        h, w = gray.shape
        if w < 12 or h < 12:
            raise ValueError("ROI is too small.")

        defect_mask = None
        if self.use_defect_mask.get():
            if self.defect_mask_roi is None:
                raise ValueError("Defect mask is enabled but has not been built.")
            if self.defect_mask_roi.shape != gray.shape:
                raise ValueError(
                    "Defect mask shape does not match the current ROI. Rebuild the defect mask."
                )
            defect_mask = self.defect_mask_roi

        m = float(self.gradient_multiplier.get())
        threshold = float(self.gradient_threshold.get())
        margin = int(max(0, self.edge_margin_px.get()))
        max_jump = float(self.max_edge_jump_px.get())
        signed_mode = bool(self.use_signed_gradient.get())
        crossing_mode = bool(self.use_intensity_crossing.get())
        subpixel_mode = bool(self.use_subpixel.get())
        if crossing_mode and not signed_mode:
            raise ValueError(
                "50% intensity crossing requires signed gradient polarity. "
                "Enable signed gradient, or disable 50% crossing for the paper/v6 comparison mode."
            )

        # Candidate lists contain tuples: (position_float, integer_peak, strength).
        top_candidates = []
        bottom_candidates = []
        raw_peaks_by_col = {}
        kept_peaks_by_col = {}
        rejected_peaks_by_col = {}
        signed_gradients_by_col = {}
        top_candidate_peaks_by_col = {}
        bottom_candidate_peaks_by_col = {}

        for x in range(w):
            f = gray[:, x].astype(np.float64)
            signed_g = np.zeros_like(f)
            if h >= 3:
                signed_g[1:-1] = m * (f[2:] - f[:-2])
            abs_g = np.abs(signed_g)
            signed_gradients_by_col[x] = signed_g.copy()

            if signed_mode:
                # Engineering extension: retain polarity.  For a bright rod on a
                # dark background in the fixed Top->Bottom profile:
                #   Top edge:    dark -> bright => positive signed gradient.
                #   Bottom edge: bright -> dark => negative signed gradient.
                top_raw = local_maxima_above(signed_g, threshold)
                bottom_raw = local_maxima_above(-signed_g, threshold)

                if margin > 0:
                    top_raw = top_raw[(top_raw >= margin) & (top_raw <= h - 1 - margin)]
                    bottom_raw = bottom_raw[(bottom_raw >= margin) & (bottom_raw <= h - 1 - margin)]

                # Tight calibration/measurement ROIs are expected to contain
                # background above and below the filament.  The midpoint gate
                # prevents an internal texture peak from swapping edge identity.
                # v14.1 OPTIONAL OUTER-SILHOUETTE CANDIDATE GATES.
                # Restrict which gradient anchors are ELIGIBLE; gradient formula,
                # peak interpolation and v7 50% crossing stay unchanged.
                # Guides are ROI-local (top_y, bottom_y) for each x-column.
                guide = getattr(self, "v141_edge_guides_local", None)
                if guide is not None:
                    if np.asarray(guide).shape != (w, 2):
                        raise ValueError("Outer-edge guides must be (ROI width, 2).")
                    radius = float(getattr(self, "v141_gate_radius", 8.0))
                    if radius < 1.0:
                        raise ValueError("Outer-edge gate radius must be positive.")
                    top_raw = top_raw[np.abs(top_raw - guide[x, 0]) <= radius]
                    bottom_raw = bottom_raw[np.abs(bottom_raw - guide[x, 1]) <= radius]
                else:
                    # Original v11 fallback for manually selected ROI.
                    mid = 0.5 * (h - 1)
                    top_raw = top_raw[top_raw < mid]
                    bottom_raw = bottom_raw[bottom_raw > mid]

                raw_union = np.unique(np.concatenate([top_raw, bottom_raw])).astype(np.int32)
                raw_peaks_by_col[x] = raw_union.copy()

                def reject_static(peaks):
                    if defect_mask is None or len(peaks) == 0:
                        return peaks.copy(), np.empty(0, dtype=np.int32)
                    reject = defect_mask[peaks, x] > 0
                    return peaks[~reject], peaks[reject]

                top_kept, top_rej = reject_static(top_raw)
                bottom_kept, bottom_rej = reject_static(bottom_raw)
                rejected = np.unique(np.concatenate([top_rej, bottom_rej])).astype(np.int32)
                kept = np.unique(np.concatenate([top_kept, bottom_kept])).astype(np.int32)
                rejected_peaks_by_col[x] = rejected
                kept_peaks_by_col[x] = kept
                top_candidate_peaks_by_col[x] = top_kept.copy()
                bottom_candidate_peaks_by_col[x] = bottom_kept.copy()

                tc = []
                for i in top_kept:
                    pos = parabolic_subpixel_peak(signed_g, i) if subpixel_mode else float(i)
                    tc.append((float(pos), int(i), float(signed_g[i])))
                bc = []
                neg_g = -signed_g
                for i in bottom_kept:
                    pos = parabolic_subpixel_peak(neg_g, i) if subpixel_mode else float(i)
                    bc.append((float(pos), int(i), float(neg_g[i])))
                top_candidates.append(tc)
                bottom_candidates.append(bc)

            else:
                # Paper-style baseline: absolute-gradient local maxima.
                raw = local_maxima_above(abs_g, threshold)
                if margin > 0:
                    raw = raw[(raw >= margin) & (raw <= h - 1 - margin)]
                raw_peaks_by_col[x] = raw.copy()

                if defect_mask is not None and len(raw):
                    reject = defect_mask[raw, x] > 0
                    rejected = raw[reject]
                    kept = raw[~reject]
                else:
                    rejected = np.empty(0, dtype=np.int32)
                    kept = raw
                rejected_peaks_by_col[x] = rejected.copy()
                kept_peaks_by_col[x] = kept.copy()

                # In the baseline, first/last qualifying peaks remain the
                # paper-style extreme boundaries. Optional sub-pixel refinement
                # only refines their coordinates; it does not change which peaks win.
                if len(kept) >= 2:
                    iT = int(kept[0])
                    iB = int(kept[-1])
                    pT = parabolic_subpixel_peak(abs_g, iT) if subpixel_mode else float(iT)
                    pB = parabolic_subpixel_peak(abs_g, iB) if subpixel_mode else float(iB)
                    top_candidates.append([(float(pT), iT, float(abs_g[iT]))])
                    bottom_candidates.append([(float(pB), iB, float(abs_g[iB]))])
                    top_candidate_peaks_by_col[x] = np.array([iT], dtype=np.int32)
                    bottom_candidate_peaks_by_col[x] = np.array([iB], dtype=np.int32)
                else:
                    top_candidates.append([])
                    bottom_candidates.append([])
                    top_candidate_peaks_by_col[x] = np.empty(0, dtype=np.int32)
                    bottom_candidate_peaks_by_col[x] = np.empty(0, dtype=np.int32)

        # -------------------------------------------------------------
        # Select TOP / BOTTOM boundaries.
        # -------------------------------------------------------------
        top = np.full(w, np.nan, dtype=np.float64)
        bottom = np.full(w, np.nan, dtype=np.float64)
        center = np.full(w, np.nan, dtype=np.float64)
        valid = np.zeros(w, dtype=bool)
        widths = np.full(w, np.nan, dtype=np.float64)

        def strongest(cands):
            return max(cands, key=lambda t: t[2]) if cands else None

        if not self.use_continuity.get():
            for x in range(w):
                if not top_candidates[x] or not bottom_candidates[x]:
                    continue
                if signed_mode:
                    t = strongest(top_candidates[x])
                    b = strongest(bottom_candidates[x])
                else:
                    t = top_candidates[x][0]
                    b = bottom_candidates[x][0]
                T = float(t[0])
                B = float(b[0])
                if B <= T:
                    continue
                top[x] = T
                bottom[x] = B
                center[x] = 0.5 * (T + B)
                widths[x] = B - T
                valid[x] = True
        else:
            cols_with_pairs = np.array(
                [x for x in range(w) if top_candidates[x] and bottom_candidates[x]],
                dtype=np.int32,
            )
            if len(cols_with_pairs) == 0:
                raise ValueError("No vertical slice contains a usable Top/Bottom edge pair.")

            seed_target = w // 2
            seed_x = int(cols_with_pairs[np.argmin(np.abs(cols_with_pairs - seed_target))])
            seed_t = strongest(top_candidates[seed_x])
            seed_b = strongest(bottom_candidates[seed_x])
            seed_T, seed_B = float(seed_t[0]), float(seed_b[0])
            if seed_B <= seed_T:
                raise ValueError("Seed Top/Bottom edge pair is invalid.")

            top[seed_x] = seed_T
            bottom[seed_x] = seed_B
            center[seed_x] = 0.5 * (seed_T + seed_B)
            widths[seed_x] = seed_B - seed_T
            valid[seed_x] = True

            def track(direction):
                prev_T = seed_T
                prev_B = seed_B
                prev_W = seed_B - seed_T
                col_iter = range(seed_x + 1, w) if direction > 0 else range(seed_x - 1, -1, -1)

                for x in col_iter:
                    if not top_candidates[x] or not bottom_candidates[x]:
                        continue
                    best = None
                    best_cost = float("inf")
                    for t in top_candidates[x]:
                        T = float(t[0])
                        if abs(T - prev_T) > max_jump:
                            continue
                        for b in bottom_candidates[x]:
                            B = float(b[0])
                            if abs(B - prev_B) > max_jump or B <= T:
                                continue
                            W = B - T
                            cost = abs(T-prev_T) + abs(B-prev_B) + 0.25*abs(W-prev_W)
                            if cost < best_cost:
                                best_cost = cost
                                best = (T, B, W)
                    if best is None:
                        continue
                    T, B, W = best
                    top[x], bottom[x] = T, B
                    center[x], widths[x] = 0.5*(T+B), W
                    valid[x] = True
                    prev_T, prev_B, prev_W = T, B, W

            track(+1)
            track(-1)

        # -------------------------------------------------------------
        # v7 physical-edge localization.
        # The v6 logic above chooses gradient anchors only.  When enabled,
        # replace those anchor coordinates by the 50% grayscale crossings.
        # -------------------------------------------------------------
        intensity_crossing_by_col = {}
        if crossing_mode:
            for x in range(w):
                if not valid[x]:
                    continue

                top_peaks = top_candidate_peaks_by_col.get(x, np.empty(0, dtype=np.int32))
                bottom_peaks = bottom_candidate_peaks_by_col.get(x, np.empty(0, dtype=np.int32))
                if len(top_peaks) == 0 or len(bottom_peaks) == 0:
                    valid[x] = False
                    continue

                # Recover the integer gradient anchors chosen by the v6 selection
                # without altering its candidate-selection / continuity behaviour.
                top_anchor = int(top_peaks[np.argmin(np.abs(top_peaks - top[x]))])
                bottom_anchor = int(bottom_peaks[np.argmin(np.abs(bottom_peaks - bottom[x]))])

                info = fifty_percent_edge_pair(
                    gray[:, x].astype(np.float64),
                    top_anchor,
                    bottom_anchor,
                    margin=margin,
                )
                if info is not None and getattr(self, "v141_edge_guides_local", None) is not None:
                    # Reject if 50% crossing jumped away from a silhouette edge.
                    guide = self.v141_edge_guides_local[x]
                    limit = float(getattr(self, "v141_gate_radius", 8.0)) + 2.0
                    if (abs(info["y_top"] - guide[0]) > limit or
                            abs(info["y_bottom"] - guide[1]) > limit):
                        info = None
                if info is None:
                    valid[x] = False
                    top[x] = bottom[x] = center[x] = widths[x] = np.nan
                    continue

                T = float(info["y_top"])
                B = float(info["y_bottom"])
                top[x] = T
                bottom[x] = B
                center[x] = 0.5 * (T + B)
                widths[x] = B - T
                info["top_gradient_anchor_px"] = int(top_anchor)
                info["bottom_gradient_anchor_px"] = int(bottom_anchor)
                intensity_crossing_by_col[x] = info

        n_valid = int(np.sum(valid))
        if n_valid < max(10, int(0.25 * w)):
            raise ValueError(
                f"Only {n_valid}/{w} vertical slices retained valid edges. "
                "Try adjusting the measurement threshold, ROI, or engineering edge-selection options."
            )

        # -------------------------------------------------------------
        # Segmented mask + centreline rectification.
        # -------------------------------------------------------------
        mask = np.zeros((h, w), dtype=np.uint8)
        for x in range(w):
            if not valid[x]:
                continue
            yT = int(round(top[x]))
            yB = int(round(bottom[x]))
            yT = int(np.clip(yT, 0, h-1))
            yB = int(np.clip(yB, 0, h-1))
            if yB >= yT:
                mask[yT:yB + 1, x] = 255

        valid_widths = widths[valid]
        valid_centers = center[valid]
        target_center = float(np.median(valid_centers))
        rectified = np.zeros_like(mask)

        for x in range(w):
            if not valid[x]:
                continue
            shift = int(round(target_center - center[x]))
            col = mask[:, x]
            if shift == 0:
                rectified[:, x] = col
            elif shift > 0:
                if shift < h:
                    rectified[shift:, x] = col[:h-shift]
            else:
                sshift = -shift
                if sshift < h:
                    rectified[:h-sshift, x] = col[sshift:]

        # -------------------------------------------------------------
        # v10 physical conversion: working pixels -> ORIGINAL pixels -> mm.
        # IMPORTANT: top/bottom coordinates above are ROI-local coordinates.
        # First restore full 90°-CW working-image coordinates:
        #     x_work = x0 + x, y_work = y0 + y_edge
        # For an original W×H frame rotated 90° CW:
        #     x_work = H - 1 - y_original
        #     y_work = x_original
        # so the inverse is:
        #     x_original = y_work
        #     y_original = H - 1 - x_work
        # The saved ORIGINAL-camera homography is then applied to each edge.
        # -------------------------------------------------------------
        diameters = np.full(w, np.nan, dtype=np.float64)
        diameters_raw = np.full(w, np.nan, dtype=np.float64)
        top_work = np.full((w, 2), np.nan, dtype=np.float64)
        bottom_work = np.full((w, 2), np.nan, dtype=np.float64)
        top_orig = np.full((w, 2), np.nan, dtype=np.float64)
        bottom_orig = np.full((w, 2), np.nan, dtype=np.float64)
        top_mm = np.full((w, 2), np.nan, dtype=np.float64)
        bottom_mm = np.full((w, 2), np.nan, dtype=np.float64)

        raw_h, raw_w = self.image_cv_raw.shape[:2]
        valid_cols = np.flatnonzero(valid)
        if len(valid_cols):
            x_work = x0 + valid_cols.astype(np.float64)
            y_top_work = y0 + top[valid_cols]
            y_bottom_work = y0 + bottom[valid_cols]

            top_work_pts = np.column_stack([x_work, y_top_work])
            bottom_work_pts = np.column_stack([x_work, y_bottom_work])

            top_orig_pts = np.column_stack([y_top_work, (raw_h - 1.0) - x_work])
            bottom_orig_pts = np.column_stack([y_bottom_work, (raw_h - 1.0) - x_work])

            top_mm_pts = apply_homography_points(
                self.homography_original_px_to_mm, top_orig_pts
            )
            bottom_mm_pts = apply_homography_points(
                self.homography_original_px_to_mm, bottom_orig_pts
            )

            # Cross-check the coordinate convention using the homography saved
            # directly for the 90°-CW calibration view.  Differences should be
            # numerical roundoff only.
            top_mm_direct = apply_homography_points(
                self.homography_working_px_to_mm, top_work_pts
            )
            bottom_mm_direct = apply_homography_points(
                self.homography_working_px_to_mm, bottom_work_pts
            )
            max_crosscheck = max(
                float(np.max(np.linalg.norm(top_mm_pts - top_mm_direct, axis=1))),
                float(np.max(np.linalg.norm(bottom_mm_pts - bottom_mm_direct, axis=1))),
            )
            if max_crosscheck > 1e-6:
                raise ValueError(
                    f"90° coordinate-conversion cross-check failed ({max_crosscheck:.6g} mm)."
                )

            d_mm_raw = np.linalg.norm(bottom_mm_pts - top_mm_pts, axis=1)
            diameters_raw[valid_cols] = d_mm_raw
            top_work[valid_cols] = top_work_pts
            bottom_work[valid_cols] = bottom_work_pts
            top_orig[valid_cols] = top_orig_pts
            bottom_orig[valid_cols] = bottom_orig_pts
            top_mm[valid_cols] = top_mm_pts
            bottom_mm[valid_cols] = bottom_mm_pts

        # v11 derives/applies one robust target-plane factor after raw homography.
        raw_stats_for_factor = robust_summary(diameters_raw[valid])
        if self.operation_mode.get() == "calibrate":
            ref_width = float(self.known_diameter_mm.get())
            raw_median = float(raw_stats_for_factor["median"])
            if raw_median <= 0 or not np.isfinite(raw_median):
                raise ValueError("Cannot derive K_ref from raw homography median.")
            reference_factor = ref_width / raw_median
            self.reference_correction_factor_var.set(reference_factor)
            self.reference_calibration_record = {
                "calibration_type": "target_plane_reference_correction",
                "known_width_mm": ref_width,
                "raw_homography_median_mm": raw_median,
                "raw_homography_stats": raw_stats_for_factor,
                "correction_factor": float(reference_factor),
                "valid_slices": int(np.sum(valid)),
                "pixel_diameter_stats": robust_summary(widths[valid]),
                "relation": "K_ref = D_known / median(D_homography_reference)",
                "working_rotation_degrees_clockwise": 90,
                "planar_calibration_json": str(self.planar_calibration_path) if self.planar_calibration_path else None,
                "interpretation": "effective target-plane correction; not a physical camera-distance estimate",
            }
        if reference_factor is None:
            reference_factor = 1.0
        diameters[valid] = diameters_raw[valid] * float(reference_factor)
        self.reference_correction_factor = float(reference_factor)

        self.rectified_gray = gray
        self.segment_mask = mask
        self.rectified_mask = rectified
        self.top_edges = top
        self.bottom_edges = bottom
        self.centers = center
        self.valid = valid
        self.widths_px = widths
        self.diameters_mm_homography_raw = diameters_raw
        self.diameters_mm = diameters
        self.top_points_working_px = top_work
        self.bottom_points_working_px = bottom_work
        self.top_points_original_px = top_orig
        self.bottom_points_original_px = bottom_orig
        self.top_points_mm = top_mm
        self.bottom_points_mm = bottom_mm
        self.raw_peaks_by_col = raw_peaks_by_col
        self.kept_peaks_by_col = kept_peaks_by_col
        self.rejected_peaks_by_col = rejected_peaks_by_col
        self.signed_gradients_by_col = signed_gradients_by_col
        self.top_candidate_peaks_by_col = top_candidate_peaks_by_col
        self.bottom_candidate_peaks_by_col = bottom_candidate_peaks_by_col
        self.intensity_crossing_by_col = intensity_crossing_by_col

        px_stats = robust_summary(valid_widths)
        top_stats = robust_summary(top[valid])
        bottom_stats = robust_summary(bottom[valid])
        mm_raw_stats = robust_summary(diameters_raw[valid])
        mm_stats = robust_summary(diameters[valid])
        rejected_total = int(sum(len(v) for v in rejected_peaks_by_col.values()))
        mask_line = (
            f"Defect mask: ON | rejected peaks: {rejected_total}\n"
            if defect_mask is not None else "Defect mask: OFF\n"
        )
        continuity_line = (
            f"Continuity: ON | max jump: {max_jump:.1f} px\n"
            if self.use_continuity.get() else "Continuity: OFF\n"
        )
        edge_line = (
            "Edge selection: SIGNED polarity (+Top / -Bottom)\n"
            if signed_mode else "Edge selection: PAPER absolute-gradient extremes\n"
        )
        localization_line = (
            "Edge localization: 50% intensity crossing | linear sub-pixel interpolation\n"
            if crossing_mode
            else (
                "Edge localization: strongest gradient peak | 3-point parabolic sub-pixel\n"
                if subpixel_mode
                else "Edge localization: strongest gradient peak | integer pixel\n"
            )
        )
        calibration_line = ""
        if self.operation_mode.get() == "calibrate":
            ref = float(self.known_diameter_mm.get())
            calibration_line = (
                f"\nREFERENCE CALIBRATION\n"
                f"Known width: {ref:.5f} mm\n"
                f"Raw homography median: {mm_raw_stats['median']:.5f} mm\n"
                f"Derived K_ref: {self.reference_correction_factor:.9f}\n"
                f"Corrected median: {mm_stats['median']:.5f} mm (definition, not independent validation)\n"
            )

        cal_name = self.planar_calibration_path.name if self.planar_calibration_path else "(not loaded)"

        self.result_var.set(
            f"Working orientation: 90° CW | filament horizontal\n"
            f"Slice direction: vertical columns (Top → Bottom)\n"
            f"Valid slices: {n_valid} / {w}\n"
            f"Gradient m: {m:g}\n"
            f"Threshold: {threshold:g}\n"
            f"{edge_line}{localization_line}{mask_line}{continuity_line}"
            f"ROI crop: {w} × {h} px\n"
            f"Measurement zone: {'FIXED' if self.use_fixed_roi.get() else 'MANUAL'}\n"
            f"\nPIXEL DIAMETER\n"
            f"Median: {px_stats['median']:.4f} px\n"
            f"Mean:   {px_stats['mean']:.4f} px\n"
            f"Std:    {px_stats['std']:.4f} px\n"
            f"Q1-Q3:  {px_stats['q1']:.4f} … {px_stats['q3']:.4f} px\n"
            f"\nEDGE POSITION\n"
            f"Top median:    {top_stats['median']:.4f} px | mean {top_stats['mean']:.4f} | std {top_stats['std']:.4f}\n"
            f"Top Q1-Q3:     {top_stats['q1']:.4f} … {top_stats['q3']:.4f} px\n"
            f"Bottom median: {bottom_stats['median']:.4f} px | mean {bottom_stats['mean']:.4f} | std {bottom_stats['std']:.4f}\n"
            f"Bottom Q1-Q3:  {bottom_stats['q1']:.4f} … {bottom_stats['q3']:.4f} px\n"
            f"\nPLANAR HOMOGRAPHY\n"
            f"Calibration: {cal_name}\n"
            f"Mapping: ORIGINAL camera px → calibration-plane mm\n"
            f"Raw homography median: {mm_raw_stats['median']:.5f} mm\n"
            f"\nTARGET-PLANE REFERENCE CORRECTION\n"
            f"Enabled: {'YES' if (self.use_reference_correction.get() or self.operation_mode.get() == 'calibrate') else 'NO'}\n"
            f"K_ref: {self.reference_correction_factor:.9f}\n"
            f"Relation: D_final = D_homography × K_ref\n"
            f"\nDIAMETER (FINAL)\n"
            f"Median: {mm_stats['median']:.5f} mm\n"
            f"Mean:   {mm_stats['mean']:.5f} mm\n"
            f"Std:    {mm_stats['std']:.5f} mm\n"
            f"Q1-Q3:  {mm_stats['q1']:.5f} … {mm_stats['q3']:.5f} mm\n"
            f"P05-P95: {mm_stats['p05']:.5f} … {mm_stats['p95']:.5f} mm"
            f"{calibration_line}"
        )

        self.view_mode.set("segmented")
        self._fit_view()
        self.status_var.set(
            "Processing complete. v11 measures only the selected/fixed Direct View zone, converts v7 50% crossings through "
            "the local planar homography, then applies K_ref calibrated in the same zone."
        )

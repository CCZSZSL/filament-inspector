"""v13 three-view orchestration. Uses the UNMODIFIED v11 50% edge metrology.

The v12 background subtraction is exclusively for candidate localization.
For a dark filament, I'=255-I is applied to the *current* ROI input to the
existing v11 algorithm (this reverses signed gradient without changing the
v11 estimator). All geometry remains on the unmodified working coordinates.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np

from filament_inspector.algorithms.engine import PaperMethodEngine
from filament_inspector.algorithms.edges import robust_summary
from filament_inspector.algorithms.background import find_filament_by_background
from filament_inspector.algorithms.edge_guides import estimate_outer_silhouette_guides, edge_alignment_quality

VIEW_NAMES = ("direct", "left", "right")
VIEW_LABELS = {"direct": "Direct View", "left": "Left Mirror", "right": "Right Mirror"}
VIEW_POLARITIES = {"direct": "bright", "left": "dark", "right": "dark"}
DEFAULT_ALGORITHM = {
    "gradient_multiplier_m": 15.0, "gradient_threshold": 40.0,
    "roi_edge_margin_px": 3, "use_signed_gradient_polarity": True,
    "use_50_percent_intensity_crossing": True,
    "use_subpixel_peak_localization_v6_comparison": True,
    "use_static_defect_mask": False,
    "use_edge_continuity_tracking": False, "max_edge_jump_px": 6.0,
}


class Var:
    """Tk-compatible .get()/.set() for the original unmodified v11 engine."""
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


def new_view(name):
    if name not in VIEW_NAMES: raise ValueError(name)
    return {
        "name": name, "polarity": VIEW_POLARITIES[name],
        "search_roi_xyxy": None, "profile": None,
        "profile_path": None, "expected_width_mm": 1.75,
        "width_range_mm": [1.3, 2.35],
        "approx_expected_width_px": 75.0,  # coarse gate ONLY, when uncalibrated
        "foreground_threshold": 14.0, "edge_padding_px": 14,
        "previous_y": None,
        # v14.1: candidates must lie near background-derived silhouette bounds.
        "outer_edge_constraint": True,
        "outer_edge_gate_radius_px": 8.0,
        "detection_method": "gradient" if name == "direct" else "silhouette",
        "local_scale_calibration": None,
        "seed_gray_model": None,  # v14.6.2 optional; legacy layouts remain valid
    }


def _validate_matrix(x, label):
    H = np.asarray(x, dtype=np.float64)
    if H.shape != (3, 3) or not np.all(np.isfinite(H)) or abs(np.linalg.det(H)) < 1e-12:
        raise ValueError(f"{label}: invalid 3x3 matrix")
    return H


def normalize_profile(profile, view_name):
    """Strict per-view calibration. Accept a view-specific profile or planar setup."""
    p = json.loads(json.dumps(profile))  # defensive copy
    if p.get("calibration_type") == "local_planar_homography":
        p = {
            "calibration_type": "three_view_calibration_profile_v13",
            "view_name": VIEW_LABELS[view_name],
            "working_rotation_degrees_clockwise": 90,
            "local_planar_calibration": p,
            "target_plane_reference_correction": {"enabled": False, "correction_factor_K_ref": None},
            "algorithm_settings": dict(DEFAULT_ALGORITHM),
        }
    if p.get("view_name") != VIEW_LABELS[view_name]:
        raise ValueError(f"Wrong view calibration: expected {VIEW_LABELS[view_name]}, "
                         f"got {p.get('view_name')}. Do not reuse Direct calibration for mirrors.")
    if int(p.get("working_rotation_degrees_clockwise", -1)) != 90:
        raise ValueError("Working calibration must be rotated 90 degrees clockwise")
    cal = p.get("local_planar_calibration")
    if not isinstance(cal, dict): raise ValueError("Missing independent planar calibration")
    if int(cal.get("rotation_degrees_clockwise_in_ui", -1)) != 90:
        raise ValueError("Planar calibration rotation mismatch")
    H_o = _validate_matrix(cal.get("homography_original_camera_px_to_mm"), "Original H")
    H_w = _validate_matrix(cal.get("homography_calibration_view_px_to_mm"), "Working H")
    T = _validate_matrix(cal.get("transform_original_px_to_calibration_view_px"), "Rotation T")
    C = H_w @ T
    if not np.allclose(C / C[2, 2], H_o / H_o[2, 2], rtol=1e-7, atol=1e-8):
        raise ValueError("Planar calibration coordinate mappings disagree")
    ow, oh = int(cal.get("original_image_width_px", 0)), int(cal.get("original_image_height_px", 0))
    if ow < 100 or oh < 100: raise ValueError("Missing calibrated original image size")
    expected_t = np.array([[0, -1, oh-1], [1, 0, 0], [0, 0, 1.]], float)
    if not np.allclose(T/T[2,2], expected_t, atol=1e-6):
        raise ValueError("Original-to-working transform incompatible with 90 degree CW and image size")
    return p


def calibration_status(profile):
    if profile is None: return "UNCALIBRATED (pixel only)"
    corr = profile.get("target_plane_reference_correction") or {}
    k = corr.get("correction_factor_K_ref")
    if not corr.get("enabled", False) or k is None: return "PLANAR ONLY (K_ref required)"
    try:
        k = float(k)
        if not np.isfinite(k) or k <= 0: raise ValueError
    except Exception: raise ValueError("Invalid K_ref")
    return "CALIBRATED"


def local_scale(profile, cx, cy):
    H = np.asarray(profile["local_planar_calibration"]["homography_calibration_view_px_to_mm"], float)
    pts = np.array([[cx,cy,1],[cx,cy+1,1]], float) @ H.T
    pts = pts[:,:2] / pts[:,2,None]
    raw = float(np.linalg.norm(pts[1]-pts[0]))
    cor = profile.get("target_plane_reference_correction") or {}
    k = float(cor.get("correction_factor_K_ref") or 1.0) if cor.get("enabled") else 1.0
    return raw*k


def _headless_metrology(current_working, raw_shape, roi, profile, polarity,
                        operation="measure", known_mm=2.0,
                        edge_guides_working=None, edge_gate_radius=8.0):
    """Call original v11 PaperMethodEngine._run_paper_method without GUI.

    When no independent calibration exists, unit-plane homographies are supplied
    ONLY to allow original pixel-edge extraction; outputs ending *_mm are then
    intentionally discarded and NEVER represented as calibrated millimetres.
    """
    calibrated = profile is not None
    v = SimpleNamespace()
    if polarity == "dark":
        gray = cv2.cvtColor(current_working, cv2.COLOR_BGR2GRAY)
        v.image_cv = cv2.cvtColor(cv2.bitwise_not(gray), cv2.COLOR_GRAY2BGR)
    else:
        v.image_cv = current_working
    v.image_cv_raw = np.empty((raw_shape[0], raw_shape[1], 1), dtype=np.uint8)
    x0,y0,x1,y1 = roi
    v.roi_points = [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    if edge_guides_working is not None:
        gate = np.asarray(edge_guides_working, dtype=np.float64)
        if gate.shape != (x1-x0+1, 2) or not np.all(np.isfinite(gate)):
            raise ValueError("Invalid v14.1 silhouette-edge guides")
        v.v141_edge_guides_local = gate - np.array([y0,y0])
        v.v141_gate_radius = float(edge_gate_radius)
    v._roi_bounds = lambda: PaperMethodEngine._roi_bounds(v)
    v._fit_view = lambda: None
    v.result_var, v.status_var, v.view_mode = Var(), Var(), Var("original")
    v.use_defect_mask = Var(False)
    v.defect_mask_roi = None
    cfg = dict(DEFAULT_ALGORITHM)
    if calibrated: cfg.update(profile.get("algorithm_settings") or {})
    v.gradient_multiplier = Var(float(cfg["gradient_multiplier_m"]))
    v.gradient_threshold = Var(float(cfg["gradient_threshold"]))
    v.edge_margin_px = Var(int(cfg["roi_edge_margin_px"]))
    v.use_signed_gradient = Var(True)
    v.use_intensity_crossing = Var(True)
    v.use_subpixel = Var(bool(cfg["use_subpixel_peak_localization_v6_comparison"]))
    v.use_continuity = Var(bool(cfg["use_edge_continuity_tracking"]))
    v.max_edge_jump_px = Var(float(cfg["max_edge_jump_px"]))
    v.use_fixed_roi = Var(False)
    v.use_reference_correction = Var(False)
    v.reference_correction_factor_var = Var(1.0)
    v.known_diameter_mm = Var(float(known_mm))
    v.operation_mode = Var(operation)
    v.planar_calibration_path = Path("view-specific-calibration") if calibrated else None
    v.reference_calibration_record = {}
    if calibrated:
        cal = profile["local_planar_calibration"]
        v.homography_original_px_to_mm = np.asarray(cal["homography_original_camera_px_to_mm"],float)
        v.homography_working_px_to_mm = np.asarray(cal["homography_calibration_view_px_to_mm"],float)
        if operation == "measure" and calibration_status(profile) == "CALIBRATED":
            v.reference_correction_factor_var.set(float(profile["target_plane_reference_correction"]["correction_factor_K_ref"]))
            v.use_reference_correction.set(True)
    else:
        # Pixel-coordinate identity, NOT physical calibration.
        h = raw_shape[0]
        v.homography_working_px_to_mm = np.eye(3)
        v.homography_original_px_to_mm = np.array([[0,-1,h-1],[1,0,0],[0,0,1]],float)
    PaperMethodEngine._run_paper_method(v,
        reference_factor=(float(v.reference_correction_factor_var.get()) if operation == "measure" else None))
    # v11's reference_factor argument bypasses stored K, so explicitly supply it.
    return v


def run_metrology(current_working, raw_shape, roi, profile, polarity,
                  operation="measure", known_mm=2.0,
                  edge_guides_working=None, edge_gate_radius=8.0):
    if profile is not None:
        dim = profile["local_planar_calibration"]
        if tuple(raw_shape[:2]) != (int(dim["original_image_height_px"]),int(dim["original_image_width_px"])):
            raise ValueError(f"Image size {raw_shape[1]}x{raw_shape[0]} does not match "
                             f"{int(dim['original_image_width_px'])}x{int(dim['original_image_height_px'])} calibration")
    v = _headless_metrology(current_working,raw_shape,roi,profile,polarity,operation,known_mm,
                            edge_guides_working=edge_guides_working,
                            edge_gate_radius=edge_gate_radius)
    valid = np.asarray(v.valid,dtype=bool)
    pixel = robust_summary(v.widths_px[valid])
    status = calibration_status(profile)
    out = {
        "valid_slices": int(valid.sum()), "total_slices": int(valid.size),
        "pixel_stats": pixel, "calibration_status": status,
        "physical_stats": (robust_summary(v.diameters_mm[valid]) if
                           profile is not None and (status == "CALIBRATED" or operation == "calibrate") else None),
        "raw_homography_stats": (robust_summary(v.diameters_mm_homography_raw[valid]) if profile is not None else None),
        "K_ref": v.reference_correction_factor if (status=="CALIBRATED" or operation=="calibrate") else None,
        "roi": list(roi), "mode": operation,
    }
    return out, v


def locate_view(current_working, background_working, view, profile=None):
    if current_working.shape != background_working.shape:
        raise ValueError("Current / background sizes mismatch")
    box = view.get("search_roi_xyxy")
    if box is None: raise ValueError("SEARCH ROI not configured")
    compatible = False
    if profile is not None:
        dim = profile["local_planar_calibration"]
        compatible = (current_working.shape[:2] ==
                      (int(dim["original_image_width_px"]), int(dim["original_image_height_px"])))
    if compatible:
        midx,midy = (box[0]+box[2])/2, (box[1]+box[3])/2
        mm_per_px = local_scale(profile,midx,midy)
    else:
        # Independent per-view *approximate* pixel width gate only.
        # Not a homography and not used to report a diameter.
        mm_per_px = float(view["expected_width_mm"])/float(view["approx_expected_width_px"])
    c = cv2.cvtColor(current_working,cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(background_working,cv2.COLOR_BGR2GRAY)
    return find_filament_by_background(
        c,b,tuple(box),mm_per_px=mm_per_px,
        width_range_mm=tuple(view["width_range_mm"]),
        foreground_threshold=float(view["foreground_threshold"]),
        edge_padding_px=int(view["edge_padding_px"]),
        expected_width_mm=float(view["expected_width_mm"]),
        polarity=view["polarity"],previous_y=view.get("previous_y"))


def heuristic_confidence(found, valid_fraction=None):
    """A quality heuristic in [0,1], NOT a calibrated statistical probability."""
    contrast = max(0.0,float(found["foreground_contrast"]))
    threshold = max(1.0,float(found["adaptive_threshold_gray"]))
    coverage = float(np.clip(found["foreground_column_coverage"],0,1))
    uniqueness = 1.0 if int(found["candidates"]) == 1 else 0.7
    base = (0.55*coverage + 0.45*min(1,contrast/max(20,2*threshold))) * uniqueness
    if valid_fraction is not None:
        base = 0.7*base+0.3*min(1,max(0,valid_fraction)/0.9)
    return float(np.clip(base,0,1))


def process_view(current_working, background_working, raw_shape, view, *,
                 operation="measure", known_mm=2.0):
    """Independently localize and measure one view; never replace its calibration.

    If nearby high-contrast hardware contaminates a v12 padded ROI, the original
    v11 estimator is retried using smaller foreground-derived ROIs. These are
    geometric *ROI* alternatives; no change to gradient/50% edge computation.
    """
    profile = view.get("profile")
    if profile is not None:
        dim = profile["local_planar_calibration"]
        if tuple(raw_shape[:2]) != (int(dim["original_image_height_px"]),
                                   int(dim["original_image_width_px"])):
            raise ValueError("Image does not match view calibration resolution; physical measurement blocked. "
                             "Use the full-resolution original photo.")
    found = locate_view(current_working,background_working,view,profile)
    roi = [int(x) for x in found["measurement_roi_xyxy"]]
    attempts = [roi]
    yy0, yy1 = found["foreground_rough_edge_y"]
    sx0,sy0,sx1,sy1 = found["search_xyxy"]
    # Keep original v12 selection first. Only fallback to tighter, still
    # background-padded ROIs if the original estimator explicitly rejects it.
    for before,after in [(12,8),(10,6),(10,4),(8,4),(6,4),(4,4)]:
        candidate = [sx0,max(sy0,yy0-before),sx1,min(sy1,yy1+after)]
        if candidate[3]-candidate[1] >= 12 and candidate not in attempts:
            attempts.append(candidate)
    # v14.1 uses background only to guide anchor SELECTION, never to determine
    # physical subpixel position or corrected diameter.
    use_outer = bool(view.get("outer_edge_constraint", True))
    guide_data = None
    if use_outer:
        guide_data = estimate_outer_silhouette_guides(
            current_working, background_working, roi,
            found["foreground_rough_edge_y"], polarity=view["polarity"],
            radius_px=float(view.get("outer_edge_gate_radius_px", 8.0)),
        )
    errors = []
    for candidate in attempts:
        try:
            m,engine = run_metrology(current_working,raw_shape,candidate,profile,
                                     view["polarity"],operation,known_mm,
                                     edge_guides_working=(guide_data["guides"] if guide_data else None),
                                     edge_gate_radius=float(view.get("outer_edge_gate_radius_px",8.0)))
            if m["valid_slices"] / max(1,m["total_slices"]) < 0.75:
                errors.append(f"{candidate}: low valid-slice fraction")
                continue
            m["roi_adjusted"] = candidate != roi
            if guide_data is not None:
                quality = edge_alignment_quality(engine, candidate, guide_data,
                    float(view.get("outer_edge_gate_radius_px", 8.0)))
                if not quality["passed"]:
                    errors.append(f"{candidate}: outer-edge validation failed: {quality['reason']}")
                    continue
                m["outer_edge_validation"] = quality
                m["outer_edge_constraint"] = True
            else:
                m["outer_edge_validation"] = {"passed": False, "reason": "CONSTRAINT DISABLED"}
                m["outer_edge_constraint"] = False
            m["confidence_heuristic"] = heuristic_confidence(found,m["valid_slices"]/m["total_slices"])
            m["note"] = ("Nearby background contaminated original ROI; tried tighter "
                         "background margin with unchanged v11 estimator." if candidate != roi else "")
            return found,m,engine
        except ValueError as exc:
            errors.append(f"{candidate}: {exc}")
    raise ValueError("Localization succeeded but OUTER-EDGE metrology did not validate. "
                     + (errors[0] if errors else "No valid candidate"))

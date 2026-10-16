"""Background-assisted localization, extracted without estimator changes."""
from __future__ import annotations
import cv2
import numpy as np

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

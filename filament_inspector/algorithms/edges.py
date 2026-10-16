"""Original gradient, subpixel crossing, geometry and statistics helpers.

Extracted unchanged from the v11 paper-method implementation.
"""
import math
import cv2
import numpy as np

def order_quad_tl_tr_br_bl(points):
    """Return supplied four points in their user-selected TL,TR,BR,BL order."""
    a = np.asarray(points, dtype=np.float32)
    if a.shape != (4, 2):
        raise ValueError("Four ROI points are required.")
    return a


def quad_output_size(pts):
    """Estimate rectified ROI width/height from four original-image corners."""
    tl, tr, br, bl = pts
    top = np.linalg.norm(tr - tl)
    bottom = np.linalg.norm(br - bl)
    left = np.linalg.norm(bl - tl)
    right = np.linalg.norm(br - tr)
    w = max(8, int(round((top + bottom) * 0.5)))
    h = max(8, int(round((left + right) * 0.5)))
    return w, h


def local_maxima_above(signal, threshold):
    """
    Return integer indices of local maxima >= threshold.
    Plateaus are handled conservatively by accepting a point if it is no
    lower than its left neighbour and strictly higher than its right, or vice versa.
    """
    s = np.asarray(signal, dtype=np.float64)
    if len(s) < 3:
        return np.empty(0, dtype=np.int32)

    mid = s[1:-1]
    left = s[:-2]
    right = s[2:]
    peaks = ((mid >= left) & (mid > right)) | ((mid > left) & (mid >= right))
    peaks &= mid >= float(threshold)
    return np.flatnonzero(peaks).astype(np.int32) + 1


def parabolic_subpixel_peak(signal, index):
    """Return a sub-pixel local-maximum position using a 3-point parabola.

    `signal` must be oriented so the desired feature is a local maximum
    (for a negative signed-gradient edge, pass -signed_gradient).
    Falls back to the integer index for endpoints, flat/degenerate peaks,
    or implausible interpolation offsets.
    """
    s = np.asarray(signal, dtype=np.float64)
    i = int(index)
    if i <= 0 or i >= len(s) - 1:
        return float(i)

    ym1 = float(s[i - 1])
    y0 = float(s[i])
    yp1 = float(s[i + 1])
    denom = ym1 - 2.0 * y0 + yp1
    if abs(denom) < 1e-12:
        return float(i)

    delta = 0.5 * (ym1 - yp1) / denom
    if not math.isfinite(delta) or abs(delta) > 1.0:
        return float(i)
    return float(i + delta)


def linear_intensity_crossing(profile, level, start, stop, rising):
    """Find the first directed grayscale crossing and linearly interpolate it.

    The profile coordinate increases Top -> Bottom.  For the Top edge, `rising`
    is True (dark -> bright).  For the Bottom edge it is False (bright -> dark).
    `start` and `stop` bound the transition region; the returned coordinate lies
    between two neighbouring integer pixels.
    """
    f = np.asarray(profile, dtype=np.float64)
    if len(f) < 2 or not math.isfinite(level):
        return None

    lo = max(0, int(math.floor(start)))
    hi = min(len(f) - 2, int(math.ceil(stop)))
    if hi < lo:
        return None

    for i in range(lo, hi + 1):
        a = float(f[i])
        b = float(f[i + 1])
        delta = b - a
        if rising:
            crossed = (a <= level <= b) and delta > 0.0
        else:
            crossed = (a >= level >= b) and delta < 0.0
        if not crossed:
            continue

        frac = (float(level) - a) / delta
        if math.isfinite(frac) and -1e-9 <= frac <= 1.0 + 1e-9:
            return float(i + np.clip(frac, 0.0, 1.0))
    return None


def fifty_percent_edge_pair(profile, top_anchor, bottom_anchor, margin=3):
    """Localize Top/Bottom edges using the v7 50% intensity-crossing rule.

    Signed-gradient selection has already supplied one Top and one Bottom anchor.
    Those anchors define the approximate rod span only; they are NOT returned as
    the physical edges.

    Intensity estimates:
      * I_bg_top / I_bg_bottom: median of the outer ROI border samples.
      * I_rod: median of the central 60% of the anchor-to-anchor rod interval.

    The central median deliberately ignores local rim/halo layers.  Around each
    gradient anchor a search window large enough to include nearby same-polarity
    edge layers is scanned in the physical Top->Bottom direction.  The first 50%
    crossing is then linearly interpolated to sub-pixel position.
    """
    f = np.asarray(profile, dtype=np.float64)
    h = len(f)
    t = float(top_anchor)
    b = float(bottom_anchor)
    if h < 8 or not (math.isfinite(t) and math.isfinite(b)) or b <= t + 3.0:
        return None

    # The ROI edge margin is also a useful indication of how much clean outer
    # background the user intentionally left. Keep at least 3 pixels when possible.
    bg_n = max(2, int(max(3, margin)))
    bg_n = min(bg_n, max(2, h // 4))
    i_bg_top = float(np.median(f[:bg_n]))
    i_bg_bottom = float(np.median(f[h - bg_n:]))

    span = b - t
    core_lo = int(math.ceil(t + 0.20 * span))
    core_hi = int(math.floor(b - 0.20 * span))
    core_lo = max(0, min(h - 1, core_lo))
    core_hi = max(0, min(h - 1, core_hi))
    if core_hi - core_lo + 1 < 3:
        return None
    i_rod = float(np.median(f[core_lo:core_hi + 1]))

    # v6 signed polarity assumes a bright rod on darker background. Reject a
    # slice if the estimated plateaus contradict that assumption.
    if i_rod <= i_bg_top or i_rod <= i_bg_bottom:
        return None

    i_edge_top = 0.5 * (i_bg_top + i_rod)
    i_edge_bottom = 0.5 * (i_bg_bottom + i_rod)

    # Scale the search window with apparent rod diameter, but cap it so an
    # unrelated internal texture transition cannot take over the edge search.
    radius = int(round(0.15 * span))
    radius = max(8, min(16, radius))

    y_top = linear_intensity_crossing(
        f, i_edge_top, t - radius, t + radius, rising=True
    )
    y_bottom = linear_intensity_crossing(
        f, i_edge_bottom, b - radius, b + radius, rising=False
    )
    if y_top is None or y_bottom is None or y_bottom <= y_top:
        return None

    return {
        "y_top": float(y_top),
        "y_bottom": float(y_bottom),
        "i_bg_top": i_bg_top,
        "i_bg_bottom": i_bg_bottom,
        "i_rod": i_rod,
        "i_edge_top": float(i_edge_top),
        "i_edge_bottom": float(i_edge_bottom),
        "search_radius_px": int(radius),
    }


def robust_summary(values):
    v = np.asarray(values, dtype=np.float64)
    if len(v) == 0:
        return {}
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    return {
        "count": int(len(v)),
        "mean": float(np.mean(v)),
        "median": float(med),
        "std": float(np.std(v, ddof=1)) if len(v) > 1 else 0.0,
        "min": float(np.min(v)),
        "max": float(np.max(v)),
        "q1": float(q1),
        "q3": float(q3),
        "p05": float(np.percentile(v, 5)),
        "p95": float(np.percentile(v, 95)),
    }


def apply_homography_points(H, points_xy):
    """Apply a 3x3 projective homography to Nx2 floating-point points."""
    H = np.asarray(H, dtype=np.float64)
    pts = np.asarray(points_xy, dtype=np.float64)
    if H.shape != (3, 3):
        raise ValueError("Homography must be 3x3.")
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError("Points must have shape (N, 2).")
    ph = np.column_stack([pts, np.ones(len(pts), dtype=np.float64)])
    q = (H @ ph.T).T
    den = q[:, 2]
    if np.any(np.abs(den) < 1e-12):
        raise ValueError("Homography mapped a point to infinity.")
    return q[:, :2] / den[:, None]

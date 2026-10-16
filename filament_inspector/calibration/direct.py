"""Independent, deterministic helpers for v14.6.1 reference-rod K_ref calibration.

This module changes calibration metadata, not the existing v11 subpixel estimator.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import statistics

SCHEMA = 'direct_reference_multiframe_v1461'


def aggregate_direct_frames(frames, known_mm, *, requested_count=10, minimum_good=8):
    """Use median of per-frame RAW-H medians; never average corrected outputs.

    Frames: {'raw_median_mm': ..., 'valid':..., 'total':..., 'roi':...}
    with each included frame independently detected at the same fixed ROI.
    """
    ref = float(known_mm)
    if not math.isfinite(ref) or not 0.0 < ref < 20.0:
        raise ValueError('Reference diameter must be between 0 and 20 mm')
    if len(frames) < minimum_good:
        raise ValueError(f'Only {len(frames)}/{requested_count} accepted frames; need at least {minimum_good}. No calibration changed.')
    vals = [float(f['raw_median_mm']) for f in frames]
    if any(not math.isfinite(v) or v <= 0 for v in vals):
        raise ValueError('Invalid raw Homography measurement')
    r = statistics.median(vals)
    k = ref / r
    if not math.isfinite(k) or not 0.5 < k < 2.0:
        raise ValueError('K_ref outside safety range; verify profile and reference plane')
    return {
        'raw_homography_median_mm': r,
        'K_ref': k,
        'accepted_count': len(frames),
        'requested_count': int(requested_count),
        'frame_median_sample_std_mm': statistics.stdev(vals) if len(vals)>1 else 0.,
        'frame_median_min_mm': min(vals),
        'frame_median_max_mm': max(vals),
        'accepted_frames': deepcopy(frames),
    }


def corrected_profile(original_profile, aggregate, *, known_mm, sensor_size_wh,
                      roi, lens_position, exposure_us, analogue_gain, x_center):
    """Copy profile and replace only target-plane K_ref; never alter Homography."""
    if not original_profile or 'local_planar_calibration' not in original_profile:
        raise ValueError('Existing Direct Homography profile is mandatory')
    dims=original_profile['local_planar_calibration']
    if [int(dims['original_image_width_px']),int(dims['original_image_height_px'])] != list(sensor_size_wh):
        raise ValueError('Homography resolution does not match the current camera')
    profile = deepcopy(original_profile)
    record = {
        'schema': SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'known_width_mm': float(known_mm),
        'reference_uncertainty_note': 'Working reference measured with 0.02 mm-resolution vernier caliper; no micron-level accuracy claim.',
        'lens_position_commanded': float(lens_position),
        'exposure_us': int(exposure_us),
        'analogue_gain': float(analogue_gain),
        'working_rotation_degrees_clockwise':90,
        'original_image_size_wh': list(sensor_size_wh),
        'search_roi_xyxy_at_calibration':list(roi),
        'locked_center_x':float(x_center),
        'roi_width_columns':int(roi[2]-roi[0]+1),
        'same_reference_not_independent_validation':True,
        'calibration_summary': deepcopy(aggregate),
    }
    profile['target_plane_reference_correction']={
        'enabled': True,
        'correction_factor_K_ref':float(aggregate['K_ref']),
        'record':record,
        'relation':'D_final = D_homography * K_ref',
        'interpretation':'Local reference-rod calibration at fixed position; not independent validation',
    }
    return profile

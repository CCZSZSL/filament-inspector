"""v14.6.7: empirical, per-mirror local scale versus TRACKED image-Y.

Does not change optical edge detection, 40-column fusion, camera server or
physical roundness claims. This is NOT a depth sensor: Y alone cannot recover
independent out-of-plane motion. Profiles are valid only for the same physical
motion path, material, camera, and illumination geometry used for training.
"""
from __future__ import annotations
from copy import deepcopy
import math
from datetime import datetime, timezone
from filament_inspector.calibration.local import aggregate_records, CalibrationError

SCHEMA = 'mirror_y_position_scale_v1467'
MIN_Y_STEP_PX = 6.0
MAX_RELATIVE_SCALE_SHIFT = 0.05
BOUNDARY_TOLERANCE_PX = 1.5


def _finite(value, label):
    try: v = float(value)
    except (TypeError, ValueError) as e: raise CalibrationError(f'{label} must be finite') from e
    if not math.isfinite(v): raise CalibrationError(f'{label} must be finite')
    return v


def _base(cal):
    if not isinstance(cal, dict) or not cal.get('calibration_timestamp'):
        raise CalibrationError('Finish a v14.6.6 or later 10-frame mirror calibration first')
    d = _finite(cal['rod_width_mm'], 'Reference diameter')
    y = _finite(cal['search_roi_center_xy'][1], 'Reference center Y')
    p = _finite(cal['rod_median_width_px'], 'Reference pixel diameter')
    s = _finite(cal['mm_per_px'], 'Base scale')
    if min(d, p, s) <= 0 or abs(d/p - s)>1e-7:
        raise CalibrationError('Invalid base mirror scale')
    return d, y, p, s


def initial_profile(cal):
    d,y,p,s = _base(cal)
    return {'schema': SCHEMA,
        'base_calibration_timestamp': cal['calibration_timestamp'],
        'reference_diameter_mm': d,
        'base_center_y_px': y,
        'base_scale_mm_per_px': s,
        'points': [{'kind': 'base', 'tracked_center_y_px': y,
                    'fused_width_px': p, 'scale_mm_per_px': s,
                    'valid_frames': (cal.get('calibration_quality_metrics') or {}).get('valid_frames',10),
                    'timestamp': cal['calibration_timestamp']}],
        'enabled': True}


def validate_profile(profile,cal):
    d,base_y,_,base_scale = _base(cal)
    if not isinstance(profile,dict) or profile.get('schema') != SCHEMA:
        raise CalibrationError('Invalid Y-position scale profile')
    if profile.get('base_calibration_timestamp') != cal['calibration_timestamp']:
        raise CalibrationError('Y-position correction is stale after base calibration change')
    if abs(_finite(profile.get('reference_diameter_mm'), 'Profile reference') - d) > 1e-8:
        raise CalibrationError('Position reference diameter mismatch')
    if abs(_finite(profile.get('base_center_y_px'), 'Profile reference Y') - base_y) > 1e-6:
        raise CalibrationError('Position reference Y mismatch')
    if abs(_finite(profile.get('base_scale_mm_per_px'), 'Profile reference scale') - base_scale) > 1e-9:
        raise CalibrationError('Base scale changed; relearn Y-position curve')
    pts = profile.get('points')
    if not isinstance(pts, list) or not 1 <= len(pts) <= 21:
        raise CalibrationError('Invalid number of position points')
    validated=[];base_points=[]
    for pt in pts:
        if not isinstance(pt,dict):raise CalibrationError('Invalid position point')
        y = _finite(pt.get('tracked_center_y_px'), 'Position Y')
        s = _finite(pt.get('scale_mm_per_px'), 'Position scale')
        p = _finite(pt.get('fused_width_px'), 'Position pixel width')
        if s<=0 or p<=0 or abs(s-d/p)>1e-8:
            raise CalibrationError('Point scale inconsistent with reference diameter')
        if abs(s/base_scale-1)>MAX_RELATIVE_SCALE_SHIFT:
            raise CalibrationError('Point scale differs >5% from base; check same filament/optical geometry')
        if int(pt.get('valid_frames',0))<8:
            raise CalibrationError('Y-position point lacks 8-frame quality validation')
        if not 0<=y<4608:raise CalibrationError('Position Y outside rotated 4608-pixel image height')
        validated.append((y,s))
        if pt.get('kind')=='base':base_points.append((y,s))
    if len(base_points)!=1 or abs(base_points[0][0]-base_y)>1e-6 or abs(base_points[0][1]-base_scale)>1e-9:
        raise CalibrationError('Missing/mismatched base position point')
    ordered=sorted(validated)
    if any(b[0]-a[0]<MIN_Y_STEP_PX for a,b in zip(ordered,ordered[1:])):
        raise CalibrationError('Position points must be at least 6 image pixels apart')
    return ordered


def profile_ready(profile,cal):
    pts=validate_profile(profile,cal)
    cy=_base(cal)[1]
    return len(pts)>=3 and pts[0][0] <= cy-MIN_Y_STEP_PX and pts[-1][0]>=cy+MIN_Y_STEP_PX


def with_position_point(current, cal, records):
    """Returns a NEW profile. Caller must commit only if this succeeds."""
    profile = deepcopy(current) if current is not None else initial_profile(cal)
    pts=validate_profile(profile,cal)
    stats=aggregate_records(records,_base(cal)[0])
    y=_finite(stats['reference_center_y_px'], 'Tracked Y')
    scale=_finite(stats['mm_per_px'], 'Y scale')
    if abs(scale/_base(cal)[3]-1)>MAX_RELATIVE_SCALE_SHIFT:
        raise CalibrationError('Position point >5% from base scale; check imaging setup')
    if min(abs(y-previous_y) for previous_y,_ in pts)<MIN_Y_STEP_PX:
        raise CalibrationError('Position too close to an existing point (<6 px). Move filament up/down and retry')
    if len(pts)>=21:raise CalibrationError('Maximum of 21 Y-position anchors reached')
    profile['points'].append({
        'kind':'offset','tracked_center_y_px': y,
        'fused_width_px':stats['fused_pixel_width_px'],
        'scale_mm_per_px':scale,'valid_frames':stats['valid_frames'],
        'frame_std_mm':stats['frame_std_mm'],
        'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    validate_profile(profile,cal)
    return profile


def scale_at_position(profile,cal,center_y):
    """Finite scalar scale within user-observed position range; never extrapolate."""
    pts=validate_profile(profile,cal)
    if not profile_ready(profile,cal):
        raise CalibrationError('Record points ABOVE and BELOW the base center first')
    y=_finite(center_y,'Current tracked Y')
    if y < pts[0][0]-BOUNDARY_TOLERANCE_PX or y > pts[-1][0]+BOUNDARY_TOLERANCE_PX:
        return None
    y=max(pts[0][0],min(y,pts[-1][0]))
    # Interpolate the observed REFERENCE pixel width rather than mm/px.
    # This exactly compensates a locally linear width-versus-Y trend,
    # whereas direct interpolation of reciprocal scales introduces bias.
    diameter=_base(cal)[0]
    for i in range(len(pts)-1):
        a,sa=pts[i];b,sb=pts[i+1]
        if a<=y<=b:
            interpolated_px=(diameter/sa)+(diameter/sb-diameter/sa)*(y-a)/(b-a)
            return diameter/interpolated_px
    return pts[-1][1]

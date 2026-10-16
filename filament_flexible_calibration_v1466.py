"""v14.6.6 two-mirror multi-snapshot local scale calibration.

Uses v14.6.3 measure_dynamic's fused PX value; does NOT modify metrology.
All results are working-scale calibrations, not traceable accuracy validation.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
import numpy as np

FRAME_COUNT = 10
MIN_GOOD_FRAMES = 8
MIN_COLUMNS = 35
MAX_RESIDUAL_PX = 0.8
MIN_QUALITY = 0.65
MAX_CV = 0.015               # screening tolerance, NOT an absolute accuracy claim
MAX_CENTER_SPAN_PX = 20.0   # stay within the v14.6.3 local scale Y envelope


class CalibrationError(ValueError):
    pass


def check_reference(value):
    try: d = float(value)
    except (TypeError, ValueError) as exc: raise CalibrationError('Reference Diameter must be numeric') from exc
    if not math.isfinite(d) or not 0 < d < 20:
        raise CalibrationError('Reference Diameter must be finite and between 0 and 20 mm')
    return d


def frame_record(found, measurement, *, acquisition=None):
    """Extract a vetted v14.6.3 40-column PIXEL fusion, never raw column means."""
    fusion = measurement.get('fusion') or {}
    px = float(fusion.get('value', float('nan')))
    n = int(fusion.get('valid_columns') or 0)
    residual = float(fusion.get('edge_fit_residual_median_px', float('nan')))
    score = float(measurement.get('confidence_heuristic', float('nan')))
    center_y = float(found.get('center_y', float('nan')))
    if fusion.get('unit') != 'px': raise CalibrationError('Calibration requires uncalibrated PX fusion')
    if not math.isfinite(px) or px <= 0: raise CalibrationError('Invalid fused pixel width')
    if n < MIN_COLUMNS or int(fusion.get('total_columns') or 0) != 40:
        raise CalibrationError(f'Only {n}/40 valid fusion columns; require ≥{MIN_COLUMNS}')
    if not math.isfinite(residual) or residual > MAX_RESIDUAL_PX:
        raise CalibrationError(f'Edge fit residual {residual:.3f}px > {MAX_RESIDUAL_PX}px')
    if not math.isfinite(score) or score < MIN_QUALITY:
        raise CalibrationError(f'Detection quality {score:.3f} < {MIN_QUALITY}')
    if not math.isfinite(center_y): raise CalibrationError('Missing tracked center Y')
    return {'fused_width_px':px, 'valid_columns':n, 'quality_score':score,
            'edge_fit_residual_px':residual, 'tracked_center_y_px':center_y,
            'acquisition':dict(acquisition or {})}


def aggregate_records(records, diameter, *, min_frames=MIN_GOOD_FRAMES):
    """Robust median across frames, rejecting moving/outlier frames.

    Returns aggregate with scale and stability statistics, or raises before
    any caller state is mutated. Each accepted record must come from a NEW PNG.
    """
    d = check_reference(diameter)
    if len(records) > FRAME_COUNT: raise CalibrationError('Too many captured frames')
    if len(records) < min_frames: raise CalibrationError(f'Need ≥{min_frames} valid frames (got {len(records)})')
    widths=np.asarray([r['fused_width_px'] for r in records],dtype=float)
    ys=np.asarray([r['tracked_center_y_px'] for r in records],dtype=float)
    if not np.all(np.isfinite(widths)) or np.any(widths<=0) or not np.all(np.isfinite(ys)):
        raise CalibrationError('Invalid calibration samples')
    med=float(np.median(widths));mad=float(np.median(np.abs(widths-med)))
    robust_sigma=1.4826*mad
    # Drop unusually displaced filament positions and gross frame outliers.
    inliers=(np.abs(widths-med)<=max(.5,4*robust_sigma)) & (np.abs(ys-np.median(ys))<=MAX_CENTER_SPAN_PX)
    accepted=[r for r,ok in zip(records,inliers) if ok]
    if len(accepted)<min_frames:
        raise CalibrationError(f'Only {len(accepted)}/{len(records)} inlier frames (need {min_frames})')
    widths=np.asarray([r['fused_width_px'] for r in accepted],float)
    p=float(np.median(widths))
    sd=float(np.std(widths,ddof=1)) if len(widths)>1 else 0.0
    cv=sd/p
    if cv>MAX_CV:raise CalibrationError(f'Frame repeatability CV {cv*100:.3f}% exceeds {MAX_CV*100:.2f}%')
    return {'reference_diameter_mm':d,'fused_pixel_width_px':p,'mm_per_px':d/p,
            'valid_frames':len(accepted),'attempted_frames':FRAME_COUNT,
            'rejected_frames':FRAME_COUNT-len(accepted),
            'frame_std_px':sd,'frame_std_mm':sd*d/p,'frame_cv_percent':cv*100,
            'frame_mad_px':float(np.median(np.abs(widths-p))),
            'pixel_min':float(np.min(widths)),'pixel_max':float(np.max(widths)),
            'median_valid_columns':float(np.median([r['valid_columns'] for r in accepted])),
            'median_edge_fit_residual_px':float(np.median([r['edge_fit_residual_px'] for r in accepted])),
            'median_quality_score':float(np.median([r['quality_score'] for r in accepted])),
            'reference_center_y_px':float(np.median([r['tracked_center_y_px'] for r in accepted]))}


def make_scale(view, diameter, records, *, image_size_wh, camera_settings=None):
    """Create legacy-compatible mirror_local_scale record for measure_dynamic()."""
    if view.get('name') not in ('left','right'):raise CalibrationError('Only mirror views may be calibrated')
    roi=view.get('search_roi_xyxy')
    if not roi or int(roi[2])-int(roi[0])+1!=40:raise CalibrationError('Missing 40-column mirror ROI')
    result=aggregate_records(records,diameter)
    return {'schema':'mirror_local_scale_v142','calibration_version':'v14.6.6',
            'view_name':view['name'].title()+' Mirror',
            'working_rotation_degrees_clockwise':90,
            'original_image_size_wh':[int(x) for x in image_size_wh],
            'rod_width_mm':result['reference_diameter_mm'],
            'rod_median_width_px':result['fused_pixel_width_px'],
            'rod_pixel_std':result['frame_std_px'],
            'rod_valid_slices':int(result['median_valid_columns']), 'rod_total_slices':40,
            'mm_per_px':result['mm_per_px'],
            'search_roi_xyxy_at_calibration':list(roi),
            'search_roi_center_xy':[(roi[0]+roi[2])*0.5,result['reference_center_y_px']],
            'method':'v1463_dynamic_seed_40column_fused_projection_10_frame_robust_median',
            'calibration_timestamp':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'camera_settings':dict(camera_settings or {}),
            'calibration_quality_metrics':result,
            'reference_type':'working_calibration', 'accuracy_validated':False,
            'note':'Working Calibration; a nominal filament reference is not an absolute diameter standard. ±0.01mm target is NOT validated.'}


def validate_scale(cal, *, view_name, image_size_wh=None):
    if not isinstance(cal,dict) or cal.get('view_name')!=view_name.title()+' Mirror':
        raise CalibrationError(f'Invalid / mismatched {view_name} calibration record')
    scale=float(cal.get('mm_per_px',float('nan')))
    if not math.isfinite(scale) or scale<=0:raise CalibrationError('Invalid scale')
    roi=cal.get('search_roi_xyxy_at_calibration')
    center=cal.get('search_roi_center_xy')
    if not isinstance(roi,list) or len(roi)!=4 or not isinstance(center,list) or len(center)!=2:
        raise CalibrationError('Invalid scale ROI / center')
    if image_size_wh is not None and list(image_size_wh)!=cal.get('original_image_size_wh'):
        raise CalibrationError('Calibration image size mismatch')
    if int(cal.get('working_rotation_degrees_clockwise',-1))!=90:
        raise CalibrationError('Calibration rotation mismatch')
    return scale

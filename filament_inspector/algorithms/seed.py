"""v14.6.2 — grayscale, sample-guided silhouette localization and subpixel metrology.

Samples are only location/intensity cues. Original GRAY8 local, independent-
polarity 50% edge crossings perform measurement. No inferred RGB/colour data.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import cv2
import numpy as np
from filament_inspector.algorithms.silhouette import _runs, measure_silhouette
from filament_inspector.algorithms.edges import robust_summary
from filament_inspector.algorithms.views import calibration_status

SCHEMA = 'seed_guided_gray_v1462'

class SeedDetectionError(ValueError):
    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


def gray(image):
    if image.ndim == 2: return image
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raise ValueError('Expected single-channel gray or three-channel working image')


def sample_record(image, rect, search, *, margin=2):
    """Sample only pixels physically inside the current 40-column search band."""
    g=gray(image)
    x0,y0,x1,y1=map(int,rect)
    sx0,sy0,sx1,sy1=map(int,search)
    if not (sx0+margin <= x0 < x1 <= sx1-margin and
            sy0+margin <= y0 < y1 <= sy1-margin):
        raise ValueError('Sample must lie INSIDE the selected view search ROI, with a 2 px margin')
    if x1-x0<3 or y1-y0<3:
        raise ValueError('Sample rectangle must be at least 4 × 4 pixels')
    pixels=g[y0:y1+1,x0:x1+1].ravel()
    median=float(np.median(pixels))
    mad=float(np.median(np.abs(pixels.astype(float)-median)))
    h=np.bincount(pixels,minlength=256).astype(int).tolist()
    return {'rect_xyxy':[x0,y0,x1,y1], 'median':median, 'mad':mad,
            'intensity_histogram_256_bins':h, 'pixel_count':int(pixels.size)}


def build_model(samples, *, image_shape=None, acquisition=None):
    if not 1<=len(samples)<=3:
        raise ValueError('Supply 1–3 interior filament samples for one view')
    hist=np.sum([np.asarray(s['intensity_histogram_256_bins'],dtype=np.int64) for s in samples],axis=0)
    if hist.shape!=(256,) or np.any(hist<0) or hist.sum()<16:
        raise ValueError('Invalid sample histogram')
    # Smooth empirical probability to tolerate sensor noise. This is NOT a
    # foreground/background probability, and is never used as a width estimate.
    smooth=cv2.GaussianBlur(hist.astype(np.float32).reshape(1,-1),(0,0),2.5).ravel()
    smooth/=max(float(smooth.max()),1e-9)
    data={'schema':SCHEMA,'modality':'8-bit_grayscale_only','samples':deepcopy(samples),
          'histogram_256_bins':hist.tolist(),
          'median':float(np.searchsorted(np.cumsum(hist),hist.sum()/2)),
          'mad':float(np.median([s['mad'] for s in samples])),
          'likelihood_curve_256':smooth.tolist(),
          'working_shape_hw':list(image_shape) if image_shape is not None else None,
          'acquisition':deepcopy(acquisition) if acquisition else None,
          'created_utc':datetime.now(timezone.utc).isoformat()}
    return data


def validate_model(model, working_shape, search, acquisition=None, *, position_independent=False):
    if model.get('schema')!=SCHEMA: raise ValueError('Invalid seed model schema')
    if model.get('working_shape_hw') is not None and list(working_shape)!=list(model['working_shape_hw']):
        raise ValueError('Seed image resolution mismatch; reselect samples')
    if not position_independent:
        for s in model['samples']:
            x0,y0,x1,y1=s['rect_xyxy'];sx0,sy0,sx1,sy1=search
            if not (sx0<=x0<x1<=sx1 and sy0<=y0<y1<=sy1):
                raise ValueError('Seed sample no longer lies inside the view ROI; reselect samples')
    saved=model.get('acquisition')
    if saved and acquisition:
        # When provenance is available, do not let sample intensities silently
        # survive a focus or exposure change. Different capture timestamps fine.
        for k in ('focus_epoch','settings_epoch','session','exposure_us','analogue_gain','lens_position'):
            if k in saved and k in acquisition and str(saved[k])!=str(acquisition[k]):
                raise ValueError(f'Seed intensity model was captured with a different {k}; reselect samples')


def locate_seeded(current, background, view, *, acquisition=None):
    """Find coherent outer contours using absolute difference + sample cues.

    Locate only. Width and physical measurement are generated later from original
    gray 50% transitions and independently calibrated geometry.
    """
    c0=gray(current);b0=gray(background)
    if c0.shape!=b0.shape: raise ValueError('Current/background image sizes differ')
    box=view.get('search_roi_xyxy')
    if box is None:raise SeedDetectionError('Search ROI is not configured')
    x0,y0,x1,y1=map(int,box)
    if x0<0 or y0<0 or x1>=c0.shape[1] or y1>=c0.shape[0] or x1-x0<15 or y1-y0<30:
        raise SeedDetectionError('Invalid 40-column search ROI geometry')
    model=view.get('seed_gray_model')
    if not model:raise SeedDetectionError('No sample model: select 1–3 interior filament samples')
    validate_model(model,c0.shape,box,acquisition)
    c=c0[y0:y1+1,x0:x1+1].astype(np.float32)
    b=b0[y0:y1+1,x0:x1+1].astype(np.float32)
    diff=cv2.GaussianBlur(np.abs(c-b),(3,3),0)
    h,w=diff.shape
    # Robust illumination-shift/noise estimate from the lower score population
    # (not from the filament itself), separately for each 40-column search band.
    row_diff=np.median(diff,axis=1)
    low=row_diff[row_diff<=np.percentile(row_diff,45)]
    floor=float(np.median(low)) if low.size else 0.0
    mad=float(np.median(np.abs(low-floor))) if low.size else 0.0
    threshold=max(5.,float(view.get('foreground_threshold',14.)),floor+4.0*1.4826*mad)
    # Absolute difference is polarity invariant. Histogram is only a weak cue
    # for holes where diff is small; brightness alone cannot prove foreground.
    likelihood=np.asarray(model['likelihood_curve_256'],dtype=np.float32)
    score=likelihood[c.astype(np.uint8)]
    bg_score=likelihood[b.astype(np.uint8)]
    model_support=(score>=0.22)&(score>bg_score+0.12)&(diff>max(3.,threshold*0.22))
    foreground=(diff>threshold)|(model_support & (diff>threshold*0.35))
    # Derive seed-centered row band first. Interior highlights and shadows can
    # have either contrast polarity and may create multiple sub-bands.
    seed_ys=[]
    for s in model['samples']:
        sy0,sy1=s['rect_xyxy'][1],s['rect_xyxy'][3]
        seed_ys.extend([sy0-y0,sy1-y0])
    if not seed_ys:raise SeedDetectionError('No seed Y locations')
    seed_min,seed_max=min(seed_ys),max(seed_ys)
    approx=float(view.get('approx_expected_width_px',75.))
    if not np.isfinite(approx) or approx<8:raise SeedDetectionError('Invalid approximate width')
    # Row consensus prevents single bright surface reflection from being taken
    # as the entire rod. Use closing to connect internal low-difference stripes.
    rowmask=(np.mean(foreground,axis=1)>=0.38).astype(np.uint8)[:,None]
    k=int(np.clip(round(approx*0.28),7,49))
    rowmask=cv2.morphologyEx(rowmask,cv2.MORPH_CLOSE,np.ones((k,1),np.uint8))
    rowmask=cv2.morphologyEx(rowmask,cv2.MORPH_OPEN,np.ones((3,1),np.uint8)).ravel().astype(bool)
    candidates=[]
    for a,z in _runs(rowmask):
        width=z-a
        # Require sample Y extents inside the candidate. Seed selection defines
        # interior anchors, not the outer boundaries.
        if a>seed_min+2 or z-1<seed_max-2:continue
        if width<max(10,0.48*approx) or width>max(25,1.75*approx):continue
        if a<11 or z>=h-11:continue  # enough genuine outside for half-crossings
        cost=abs(width-approx)/approx
        if view.get('previous_y') is not None:
            cost+=0.2*abs(y0+(a+z-1)/2-float(view['previous_y']))/approx
        candidates.append((cost,a,z))
    diagnostic={'search_xyxy':list(box),'difference':diff,'foreground_mask':foreground.astype(np.uint8),
                'row_mask':rowmask,'threshold':threshold,'model_likelihood':score,
                'seed_rects':[s['rect_xyxy'] for s in model['samples']],
                'stage':'coarse localization'}
    if not candidates:
        raise SeedDetectionError('No seeded foreground band containing all samples; inspect the mask, sample placement, background and width estimate',diagnostic)
    _,a,z=min(candidates)
    rough_top,rough_bottom=y0+a,y0+z-1
    # For per-column subpixel crossings obtain the external guides from a
    # vertically closed mask, confined near the selected, seed-anchored band.
    vertical_k=int(np.clip(round(approx*0.22),7,39))
    binary=cv2.morphologyEx(foreground.astype(np.uint8),cv2.MORPH_CLOSE,
                             np.ones((vertical_k,1),np.uint8))
    binary=cv2.morphologyEx(binary,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    guides=np.full((w,2),np.nan,dtype=float)
    tolerance=max(8,int(round(approx*0.27)))
    for xi in range(w):
        options=[]
        for aa,zz in _runs(binary[:,xi].astype(bool)):
            if aa>seed_min+max(2,tolerance//2) or zz-1<seed_max-max(2,tolerance//2):continue
            if abs(aa-a)>tolerance or abs((zz-1)-(z-1))>tolerance:continue
            options.append((abs(aa-a)+abs((zz-1)-(z-1)),aa,zz))
        if options:
            _,aa,zz=min(options)
            guides[xi]=[y0+aa,y0+zz-1]
    good=np.isfinite(guides).all(axis=1)
    diagnostic.update({'foreground_mask':binary,'guides':guides,'rough_edges':[rough_top,rough_bottom],
                       'stage':'column contour guides'})
    if int(good.sum())<max(12,int(np.ceil(w*0.65))):
        raise SeedDetectionError(f'Only {int(good.sum())}/{w} coherent guide columns; masks do not support stable outer edges',diagnostic)
    for j in range(2):
        col=guides[:,j]
        filled=np.where(good,col,np.median(col[good])).astype(np.float32)[None,:]
        smoothed=cv2.medianBlur(filled,5).ravel()
        guides[good,j]=smoothed[good]
    pad=int(np.clip(round(approx*.25),13,35))
    roi=[x0,max(y0,int(np.floor(np.percentile(guides[good,0],5)))-pad),
         x1,min(y1,int(np.ceil(np.percentile(guides[good,1],95)))+pad)]
    # Reject columns with insufficient true EXTERIOR background instead of
    # returning a plausible-looking width.
    exterior=good & (guides[:,0]-roi[1]>=11) & (roi[3]-guides[:,1]>=11)
    guides[~exterior]=np.nan
    diagnostic.update({'measurement_roi':roi,'guides':guides,'stage':'50% crossing ready'})
    if int(exterior.sum())<max(12,int(np.ceil(w*.65))):
        raise SeedDetectionError(f'Only {int(exterior.sum())}/{w} columns have >=11 px exterior background',diagnostic)
    selected=diff[a:z,:]
    result={'measurement_roi_xyxy':roi,'search_xyxy':list(box),
            'foreground_rough_edge_y':[rough_top,rough_bottom],
            'center_y':float((rough_top+rough_bottom)/2),
            'coarse_width_px':float(z-a),
            'adaptive_threshold_gray':threshold,
            'foreground_contrast':float(np.median(selected)) if selected.size else 0.,
            'foreground_column_coverage':float(np.mean(binary[a:z,:])) if z>a else 0.,
            'candidates':len(candidates),'guides_working':guides,
            'valid_guide_columns':int(exterior.sum()),
            'mask_for_diagnostics':binary,'difference_for_diagnostics':diff,
            'seed_diagnostic':diagnostic, 'seed_model_summary':{'median':model['median'],
               'mad':model['mad'],'sample_count':len(model['samples'])}}
    return result


def _direct_h_widths(engine, roi, profile):
    """Apply original working-coordinate H to every accepted real subpixel pair."""
    cal=profile['local_planar_calibration']
    H=np.asarray(cal['homography_calibration_view_px_to_mm'],dtype=float)
    valid=np.asarray(engine.valid,dtype=bool)
    idx=np.flatnonzero(valid)
    if len(idx)==0:raise ValueError('No valid edge pairs for homography')
    x=roi[0]+idx.astype(float)
    y_top=roi[1]+np.asarray(engine.top_edges)[idx]
    y_bottom=roi[1]+np.asarray(engine.bottom_edges)[idx]
    tops=np.stack([x,y_top,np.ones_like(x)],axis=1)@H.T
    bottoms=np.stack([x,y_bottom,np.ones_like(x)],axis=1)@H.T
    ok=(np.abs(tops[:,2])>1e-12)&(np.abs(bottoms[:,2])>1e-12)
    transformed=np.full(idx.size,np.nan)
    transformed[ok]=np.linalg.norm(tops[ok,:2]/tops[ok,2,None]-bottoms[ok,:2]/bottoms[ok,2,None],axis=1)
    ok=ok & np.isfinite(transformed) & (transformed>0)
    valid[idx[~ok]]=False
    engine.valid=valid
    raw=np.full(len(valid),np.nan)
    raw[idx[ok]]=transformed[ok]
    correction=profile.get('target_plane_reference_correction') or {}
    k=float(correction.get('correction_factor_K_ref') or 1.0) if correction.get('enabled') else 1.0
    return raw,k


def measure_seeded(current, background, raw_shape, view, *, acquisition=None):
    try:
        found=locate_seeded(current,background,view,acquisition=acquisition)
        # v14.2 original-gray independent-direction crossing: top and bottom
        # may have DIFFERENT brightness polarity in the same column.
        # Does not re-use the legacy signed-gradient-polarity assumption.
        m,e=measure_silhouette(current,raw_shape,found,dict(view,local_scale_calibration=
                                                               (view.get('local_scale_calibration') if view['name']!='direct' else None)))
        profile=view.get('profile')
        if view['name']=='direct':
            if profile is not None:
                raw_wh=(int(raw_shape[1]),int(raw_shape[0]))
                cal=profile['local_planar_calibration']
                if raw_wh!=(int(cal['original_image_width_px']),int(cal['original_image_height_px'])):
                    raise SeedDetectionError('Direct calibration resolution mismatch; physical mm blocked',found['seed_diagnostic'])
                raw,k=_direct_h_widths(e,m['roi'],profile)
                # Any rejected homography coordinate also invalidates its
                # corresponding pixel result (never count it as accepted).
                m['valid_slices']=int(np.sum(e.valid))
                m['pixel_stats']=robust_summary(np.asarray(e.widths_px)[e.valid])
                accepted=raw[np.asarray(e.valid,dtype=bool)]
                if accepted.size<max(12,int(.65*len(e.valid))):
                    raise SeedDetectionError('Too few valid H-transformed edge pairs',found['seed_diagnostic'])
                m['raw_homography_stats']=robust_summary(accepted)
                e.diameters_mm_homography_raw=raw
                if calibration_status(profile)=='CALIBRATED':
                    m['physical_stats']=robust_summary(accepted*k)
                    e.diameters_mm=raw*k
                    e.reference_correction_factor=k
                    m['K_ref']=k
                    m['calibration_status']='DIRECT H × K_ref (original gray half-crossing)'
                else:
                    m['physical_stats']=None
                    e.diameters_mm=None
                    m['calibration_status']='DIRECT H only; K_ref not calibrated (pixel display)'
            else:
                m['calibration_status']='DIRECT uncalibrated (pixel only)'
        # Mirror scaling, valid in the same local zone only, is inherited from
        # v14.2 and deliberately never replaced by a Direct H/K_ref.
        if m['physical_stats'] is not None and view['name']!='direct':
            scale=float(m['scale_mm_per_px'])
            e.diameters_mm=np.where(e.valid,e.widths_px*scale,np.nan)
            e.reference_correction_factor=1.0
        m['detection_method']='seed_guided'
        m['seed_model_summary']=found['seed_model_summary']
        m['note']='Seed-guided polarity-independent outer segmentation; original GRAY8 50% edge interpolation; verify with independent standard.'
        e.v142_method='seed_guided'
        return found,m,e
    except SeedDetectionError:
        raise
    except Exception as exc:
        diag=locals().get('found',{}).get('seed_diagnostic')
        raise SeedDetectionError(str(exc),diag) from exc

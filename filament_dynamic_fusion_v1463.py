"""v14.6.3: location-independent grayscale model, tracked ROI and robust edge fusion.

IMPORTANT: The fused 40-column result is an axial segment measurement, not
40 independent observations of the same cross-section. Any +/-0.01 mm accuracy
claim requires validation with an independent calibrated reference.
"""
from __future__ import annotations
import cv2
import numpy as np
from filament_seed_guided_v1462 import (gray, SeedDetectionError, _direct_h_widths,
                                        validate_model)
from filament_silhouette_v142 import _runs, measure_silhouette
from filament_diameter_paper_method_ui_v11_fixed_zone import robust_summary
from filament_three_view_core_v13 import calibration_status


def _robust_line(x, y):
    """IRLS Huber straight-line fit, with slope measured in pixels/pixel."""
    x=np.asarray(x,float);y=np.asarray(y,float)
    if x.size<8 or not np.isfinite(y).all():
        raise ValueError('Too few finite points for robust edge fit')
    xc=float(np.median(x))
    A=np.stack([np.ones_like(x),x-xc],axis=1)
    weights=np.ones(len(x))
    beta=np.linalg.lstsq(A,y,rcond=None)[0]
    for _ in range(12):
        r=y-A@beta
        sigma=max(0.25,1.4826*np.median(np.abs(r-np.median(r))))
        limit=max(0.7,1.5*sigma)
        weights=np.minimum(1.,limit/np.maximum(abs(r),1.e-9))
        new=np.linalg.lstsq(A*weights[:,None],y*weights,rcond=None)[0]
        if np.linalg.norm(new-beta)<1.e-6:break
        beta=new
    return beta, A@beta, float(np.median(abs(y-A@beta)))


def _transform_points(H, x, y):
    pts=np.stack([x,y,np.ones_like(x)],axis=1)@H.T
    if np.any(np.abs(pts[:,2])<1.e-12):
        raise ValueError('Invalid homogeneous coordinates in geometry')
    return pts[:,:2]/pts[:,2,None]


def fuse_edges(result, engine, view):
    """Fit both outer edges, reject gross outliers, fuse calibrated normal widths.

    Never creates millimetres from an uncalibrated view. Keeps per-column data
    and avoids treating adjacent columns as independent accuracy validation.
    """
    valid=np.asarray(engine.valid,bool).copy()
    xall=np.arange(len(valid),dtype=float)
    if valid.sum()<26:
        raise SeedDetectionError(f'Need at least 26/40 columns for fusion, got {valid.sum()}')
    xt=xall[valid]
    ty=np.asarray(engine.top_edges,float)[valid]
    by=np.asarray(engine.bottom_edges,float)[valid]
    _,pred_t,err_t=_robust_line(xt,ty)
    _,pred_b,err_b=_robust_line(xt,by)
    rt=ty-pred_t;rb=by-pred_b
    # Edge-fit residual exclusion is intentionally generous: ordinary axial
    # diameter changes should not be suppressed as if they were pixel noise.
    sigma_t=max(.25,1.4826*np.median(abs(rt-np.median(rt))))
    sigma_b=max(.25,1.4826*np.median(abs(rb-np.median(rb))))
    inlier=(abs(rt)<=max(1.5,3.5*sigma_t)) & (abs(rb)<=max(1.5,3.5*sigma_b))
    idx=np.flatnonzero(valid)[inlier]
    if len(idx)<26:
        raise SeedDetectionError(f'Only {len(idx)}/40 edge-fit inliers; fusion rejected')
    # Refit both edges on accepted inliers and use centreline tangent to define
    # thickness NORMAL to the local filament axis (not a vertical image span).
    ptop, _,err_t=_robust_line(xall[idx],np.asarray(engine.top_edges)[idx])
    pbot, _,err_b=_robust_line(xall[idx],np.asarray(engine.bottom_edges)[idx])
    slope=0.5*(ptop[1]+pbot[1])
    top_work=engine.top_edges[idx]+result['roi'][1]
    bot_work=engine.bottom_edges[idx]+result['roi'][1]
    xwork=idx+result['roi'][0]
    # For a homography the metric-plane tangent must be computed AFTER H,
    # otherwise non-uniform H scaling introduces an incorrect angle factor.
    calibrated=result.get('physical_stats') is not None
    if calibrated and view['name']=='direct':
        H=np.asarray(view['profile']['local_planar_calibration']['homography_calibration_view_px_to_mm'],float)
        pt=_transform_points(H,xwork,top_work)
        pb=_transform_points(H,xwork,bot_work)
        pc=(pt+pb)*.5
        A=np.stack([np.ones(len(idx)),xwork-xwork.mean()],axis=1)
        tang=np.linalg.lstsq(A,pc,rcond=None)[0][1]
        norm=np.linalg.norm(tang)
        if norm<1.e-12:raise SeedDetectionError('Degenerate transformed centerline')
        tangent=tang/norm
        normal=np.array([-tangent[1],tangent[0]])
        widths=np.abs(np.sum((pb-pt)*normal,axis=1))*float(result['K_ref'])
        unit='mm'
    else:
        widths=(bot_work-top_work)/np.sqrt(1+slope*slope)
        if calibrated and view['name']!='direct':
            widths*=float(result['scale_mm_per_px'])
            unit='mm'
        else:unit='px'
    if np.any(~np.isfinite(widths)) or np.any(widths<=0):
        raise SeedDetectionError('Nonfinite projected edge widths')
    med=float(np.median(widths))
    mad=float(np.median(abs(widths-med)))
    # Remove gross physically implausible outliers but do not claim averaging
    # removes systematic geometry, illumination or calibration bias.
    inliers=np.abs(widths-med)<=max(0.5 if unit=='px' else 0.006,4*1.4826*mad)
    idx=idx[inliers];widths=widths[inliers]
    if len(idx)<26:raise SeedDetectionError(f'Only {len(idx)}/40 fused-width inliers')
    # 10% symmetric trim cuts sporadic noisy edge pair estimates. For nearly
    # constant width this is a robust central estimate of the axial band.
    order=np.sort(widths)
    trim=int(np.floor(len(order)*0.1))
    core=order[trim:len(order)-trim] if trim else order
    fused=float(np.mean(core))
    return {'value':fused,'unit':unit,'valid_columns':int(len(idx)),
            'total_columns':int(len(valid)),
            'fused_method':'paired_robust_edge_fit_and_10pct_trimmed_mean_normal_width',
            'accepted_indices':idx.tolist(),
            'column_std':float(np.std(widths,ddof=1)) if len(widths)>1 else 0.,
            'column_min':float(np.min(widths)),'column_max':float(np.max(widths)),
            'edge_fit_residual_median_px':float((err_t+err_b)/2),
            'centerline_slope_px_per_px':float(slope),
            'accuracy_validated':False}


def locate_dynamic(current, background, view, *, acquisition=None):
    """Locate using current frame content; saved seed rectangle Y is NOT a gate."""
    c0=gray(current);b0=gray(background)
    if c0.shape!=b0.shape:raise SeedDetectionError('Foreground/background dimensions differ')
    box=view.get('search_roi_xyxy')
    if box is None:raise SeedDetectionError('Search ROI missing')
    x0,y0,x1,y1=map(int,box)
    if not (0<=x0<x1<c0.shape[1] and 0<=y0<y1<c0.shape[0]) or (x1-x0+1)!=40:
        raise SeedDetectionError('Expected valid 40-column fixed-X search ROI')
    model=view.get('seed_gray_model')
    if model is None:raise SeedDetectionError('First select ONE gray8 interior sample')
    # Legacy models allowed: only check settings and frame shape, NOT original
    # rectangle position. This is essential for moving filament tracking.
    validate_model(model,c0.shape,box,acquisition,position_independent=True)
    c=c0[y0:y1+1,x0:x1+1].astype(np.float32)
    b=b0[y0:y1+1,x0:x1+1].astype(np.float32)
    diff=cv2.GaussianBlur(np.abs(c-b),(3,3),0)
    h,w=diff.shape
    approx=float(view.get('approx_expected_width_px',75))
    if not (8<=approx<h-20):raise SeedDetectionError('Search band too short for expected filament width and exterior margins')
    row_diff=np.median(diff,axis=1)
    low=row_diff[row_diff<=np.percentile(row_diff,45)]
    floor=float(np.median(low)) if low.size else 0.
    mad=float(np.median(abs(low-floor))) if low.size else 0.
    threshold=max(5.,float(view.get('foreground_threshold',14)),floor+4*1.4826*mad)
    likelihood=np.asarray(model['likelihood_curve_256'],np.float32)
    if likelihood.shape!=(256,):raise SeedDetectionError('Invalid seed histogram model; reselect sample')
    fg_like=likelihood[c.astype(np.uint8)]
    bg_like=likelihood[b.astype(np.uint8)]
    support=(fg_like>=.22)&(fg_like>bg_like+.12)&(diff>max(3,threshold*.22))
    fg=(diff>threshold)|(support&(diff>threshold*.35))
    rowmask=(np.mean(fg,axis=1)>=0.38).astype(np.uint8)[:,None]
    k=int(np.clip(round(approx*.28),7,49))
    rowmask=cv2.morphologyEx(rowmask,cv2.MORPH_CLOSE,np.ones((k,1),np.uint8))
    rowmask=cv2.morphologyEx(rowmask,cv2.MORPH_OPEN,np.ones((3,1),np.uint8)).ravel().astype(bool)
    candidates=[]
    previous=view.get('previous_y')
    # Unlike 14.6.2, original seed rectangle coordinates do not feature in
    # candidate filtering. The photometric model is a soft cue only.
    for a,z in _runs(rowmask):
        width=z-a
        if not (.55*approx<=width<=1.6*approx):continue
        if a<11 or z>h-11:continue
        center=y0+(a+z-1)*.5
        region=fg_like[a:z,:]
        bregion=bg_like[a:z,:]
        contrast=diff[a:z,:]
        if contrast.size==0:continue
        # Seed histogram only helps rank candidates, not prove identity.
        photometric=float(np.mean(np.maximum(region-bregion,0)))
        coherent=float(np.mean(contrast>threshold))
        # Width is a COARSE gate. Avoid biasing diameter estimates toward the
        # nominal diameter: metric edges are measured independently later.
        cost=.85*abs(width-approx)/approx + .28*(1-photometric) + .2*(1-coherent)
        if previous is not None and np.isfinite(previous):
            # Soft temporal prior; not a hard positioning constraint. Even
            # large shifts may be recovered by global reacquisition.
            cost+=.22*min(abs(center-float(previous))/approx,3.)
        candidates.append((cost,a,z,center,photometric))
    diagnostic={'search_xyxy':list(box),'difference':diff,'foreground_mask':fg.astype(np.uint8),
                'row_mask':rowmask,'threshold':threshold,
                'model_likelihood':fg_like,
                'seed_rects':[s['rect_xyxy'] for s in model['samples']],
                'stage':'dynamic candidate localization','candidates':len(candidates)}
    if not candidates:
        raise SeedDetectionError('No coherent moving-filament candidate. Check search Y-range, background, exposure and foreground threshold.',diagnostic)
    candidates.sort(key=lambda t:t[0]);_,a,z,center,photometric=candidates[0]
    if len(candidates)>1 and abs(candidates[1][0]-candidates[0][0])<.03:
        # Ambiguous candidate is safer to reject than return wrong diameter.
        diagnostic['ambiguity_margin']=float(candidates[1][0]-candidates[0][0])
        raise SeedDetectionError('Ambiguous foreground bands; cannot uniquely track filament',diagnostic)
    vertical_k=int(np.clip(round(approx*.22),7,39))
    binary=cv2.morphologyEx(fg.astype(np.uint8),cv2.MORPH_CLOSE,np.ones((vertical_k,1),np.uint8))
    binary=cv2.morphologyEx(binary,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    guides=np.full((w,2),np.nan,dtype=float)
    tolerance=max(8,int(round(approx*.27)))
    for xi in range(w):
        opts=[]
        for aa,zz in _runs(binary[:,xi].astype(bool)):
            if abs(aa-a)>tolerance or abs((zz-1)-(z-1))>tolerance:continue
            if zz-aa<.48*approx or zz-aa>1.75*approx:continue
            opts.append((abs(aa-a)+abs(zz-z),aa,zz))
        if opts:
            _,aa,zz=min(opts)
            guides[xi]=[y0+aa,y0+zz-1]
    good=np.isfinite(guides).all(axis=1)
    diagnostic.update({'foreground_mask':binary,'guides':guides,
                       'rough_edges':[y0+a,y0+z-1],'stage':'per-column dynamic guides',
                       'selected_center_y':center})
    if good.sum()<26:raise SeedDetectionError(f'Only {good.sum()}/{w} coherent contour guides',diagnostic)
    for j in range(2):
        filled=np.where(good,guides[:,j],np.median(guides[good,j])).astype(np.float32)[None,:]
        med=cv2.medianBlur(filled,5).ravel()
        guides[good,j]=med[good]
    pad=int(np.clip(round(approx*.25),13,35))
    roi=[x0,max(y0,int(np.floor(np.percentile(guides[good,0],5)))-pad),
         x1,min(y1,int(np.ceil(np.percentile(guides[good,1],95)))+pad)]
    exterior=good & (guides[:,0]-roi[1]>=11)&(roi[3]-guides[:,1]>=11)
    guides[~exterior]=np.nan
    diagnostic.update({'guides':guides,'measurement_roi':roi,'stage':'50% crossing ready'})
    if exterior.sum()<26:
        raise SeedDetectionError(f'Only {exterior.sum()}/{w} columns have valid exterior background',diagnostic)
    selected=diff[a:z,:]
    return {'measurement_roi_xyxy':roi,'search_xyxy':list(box),
            'foreground_rough_edge_y':[y0+a,y0+z-1], 'center_y':float(center),
            'coarse_width_px':float(z-a),'adaptive_threshold_gray':float(threshold),
            'foreground_contrast':float(np.median(selected)),
            'foreground_column_coverage':float(np.mean(binary[a:z,:])),
            'candidates':len(candidates),'guides_working':guides,
            'valid_guide_columns':int(exterior.sum()),
            'mask_for_diagnostics':binary,'difference_for_diagnostics':diff,
            'seed_diagnostic':diagnostic,
            'seed_model_summary':{'median':model['median'],'mad':model['mad'],
                                  'sample_count':len(model['samples'])},
            'tracking_previous_center_y':None if previous is None else float(previous),
            'tracking_delta_y_px':None if previous is None else float(center-previous)}


def measure_dynamic(current, background, raw_shape, view, *, acquisition=None):
    found=None
    try:
        found=locate_dynamic(current,background,view,acquisition=acquisition)
        m,e=measure_silhouette(current,raw_shape,found,dict(view,local_scale_calibration=
                            view.get('local_scale_calibration') if view['name']!='direct' else None))
        profile=view.get('profile')
        if view['name']=='direct':
            if profile:
                raw_wh=(int(raw_shape[1]),int(raw_shape[0]))
                cal=profile['local_planar_calibration']
                if raw_wh!=(int(cal['original_image_width_px']),int(cal['original_image_height_px'])):
                    raise SeedDetectionError('Direct calibration resolution mismatch',found['seed_diagnostic'])
                raw,k=_direct_h_widths(e,m['roi'],profile)
                m['valid_slices']=int(np.sum(e.valid))
                m['pixel_stats']=robust_summary(np.asarray(e.widths_px)[e.valid])
                accepted=raw[np.asarray(e.valid,dtype=bool)]
                if len(accepted)<26:raise SeedDetectionError('Too few valid H-transformed edges',found['seed_diagnostic'])
                m['raw_homography_stats']=robust_summary(accepted)
                e.diameters_mm_homography_raw=raw
                if calibration_status(profile)=='CALIBRATED':
                    m['physical_stats']=robust_summary(accepted*k)
                    e.diameters_mm=raw*k
                    e.reference_correction_factor=k
                    m['K_ref']=k
                    m['calibration_status']='DIRECT H × K_ref'
                else:
                    m['physical_stats']=None;e.diameters_mm=None
                    m['calibration_status']='DIRECT planar-only: K_ref missing'
            else:m['calibration_status']='DIRECT UNCALIBRATED (pixel only)'
        else:
            # v14.2's local scale only validated fixed search-box centre, so
            # current filament could move despite passing the test. Gate scale
            # against ACTUAL detected center to avoid silently false mm.
            ref=view.get('local_scale_calibration')
            if ref is not None and m['physical_stats'] is not None:
                rc=ref.get('search_roi_center_xy',[])
                if len(rc)!=2 or abs(found['center_y']-float(rc[1]))>20:
                    m['physical_stats']=None
                    m['calibration_status']='MIRROR local scale outside ±20px validated Y; pixel only'
                    m['scale_mm_per_px']=None
            if m['physical_stats'] is not None:
                scale=float(m['scale_mm_per_px'])
                e.diameters_mm=np.where(e.valid,e.widths_px*scale,np.nan)
                e.reference_correction_factor=1.
        m['detection_method']='dynamic_seed_guided'
        m['seed_model_summary']=found['seed_model_summary']
        m['note']='Dynamic foreground localization; robust 40-column fusion on original GRAY8 edges. Accuracy NOT yet validated.'
        fusion=fuse_edges(m,e,view)
        m['fusion']=fusion
        # Do not confuse a 40-slice scatter estimate with measurement accuracy.
        m['fused_diameter']=fusion['value'];m['fused_unit']=fusion['unit']
        m['tracking_center_y_px']=found['center_y']
        m['tracking_delta_y_px']=found['tracking_delta_y_px']
        m['tracking_reference_delta_y_px']=None
        model=view.get('seed_gray_model') or {}
        if model.get('tracking_reference_center_y') is not None:
            m['tracking_reference_delta_y_px']=float(found['center_y']-model['tracking_reference_center_y'])
        e.v142_method='dynamic_seed_guided'
        return found,m,e
    except SeedDetectionError:
        raise
    except Exception as exc:
        diag=found.get('seed_diagnostic') if found else None
        raise SeedDetectionError(str(exc),diag) from exc

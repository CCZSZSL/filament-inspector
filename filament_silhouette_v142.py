"""v14.2: independent foreground silhouette detection and local mirror scales.

Method (per view): colour-aware registered background difference -> coherent
full-width foreground band -> vertical per-column outer mask boundaries ->
50% crossing on ORIGINAL current grayscale near those boundaries. Internal
reflection peaks are NOT selected. Any unconvincing column is INVALID, never
silently 'fixed' to an expected diameter.

This is an experimental optical measurement method, not an accuracy guarantee.
"""
from __future__ import annotations
from types import SimpleNamespace
import numpy as np
import cv2
from filament_diameter_paper_method_ui_v11_fixed_zone import robust_summary


def _runs(mask):
    d = np.diff(np.r_[False, np.asarray(mask, bool), False].astype(np.int8))
    return [(int(a),int(b)) for a,b in zip(np.flatnonzero(d==1),np.flatnonzero(d==-1))]


def _half_crossing(f, pos, *, inside_below, max_shift=5):
    """Neighbouring intensity plateaus yield a directed, linearly interpolated
    50% crossing. Search is local to the segmented OUTER contour, not a global
    strongest-gradient peak. Supports either contrast polarity at each edge.
    """
    from filament_diameter_paper_method_ui_v11_fixed_zone import linear_intensity_crossing
    p=int(round(pos));n=len(f)
    if p<10 or p>n-11: return None
    # Take genuinely exterior/interior samples separated from the transition.
    if inside_below:
        outside=f[p-9:p-4]; inside=f[p+4:p+9]
    else:
        inside=f[p-9:p-4]; outside=f[p+4:p+9]
    if min(len(inside),len(outside))<3: return None
    I_bg=float(np.median(outside)); I_rod=float(np.median(inside))
    if abs(I_bg-I_rod)<6.0: return None
    target=(I_bg+I_rod)*0.5
    rising=I_rod>I_bg if inside_below else I_bg>I_rod
    # Use the verified v11 crossing interpolation, but the physically guided
    # contour determines the small search window and direction.
    options=[]
    for a in range(max(1,p-max_shift),min(n-2,p+max_shift)+1):
        crossing=linear_intensity_crossing(f,target,a,a,rising=rising)
        if crossing is not None:
            options.append(crossing)
    if not options:return None
    edge=min(options,key=lambda x: abs(x-(pos-0.5)))
    if abs(edge-pos)>max_shift:return None
    return float(edge),float(target),I_bg,I_rod


def _color_difference(cur, bg):
    # RGB channel difference catches gold/other chromatic materials that may
    # have weak grayscale contrast. Remove only a uniform exposure/color shift.
    a=cur.astype(np.float32);b=bg.astype(np.float32)
    d=a-b
    bias=np.median(d.reshape(-1,3),axis=0)
    diff=np.max(np.abs(d-bias[None,None,:]),axis=2)
    return cv2.GaussianBlur(diff.astype(np.float32),(3,3),0)


def locate_silhouette(current_working,background_working,search,expected_width_px,
                      base_threshold=12.0,previous_y=None):
    if current_working.shape!=background_working.shape or current_working.ndim!=3:
        raise ValueError('Current/background images must match')
    H,W=current_working.shape[:2]
    x0,y0,x1,y1=map(int,search)
    if not (0<=x0<x1<W and 0<=y0<y1<H):raise ValueError('Invalid search ROI')
    if x1-x0<15 or y1-y0<35:raise ValueError('Search area too narrow')
    expected=float(expected_width_px)
    if not 8<=expected<=900:raise ValueError('Invalid coarse expected width (px)')
    c=current_working[y0:y1+1,x0:x1+1]
    b=background_working[y0:y1+1,x0:x1+1]
    diff=_color_difference(c,b)
    # Estimate noise from the LOWER half of row foreground scores rather than
    # the central foreground: avoids the v12 MAD failure on tight search ROIs.
    row_score=np.median(diff,axis=1)
    lower=row_score[row_score<=np.quantile(row_score,0.45)]
    floor=float(np.median(lower)) if lower.size else 0
    mad=float(np.median(np.abs(lower-floor))) if lower.size else 0
    threshold=max(float(base_threshold),floor+4.0*1.4826*mad)
    # Median signal across X; object should span most selected columns.
    mask=(row_score>threshold).astype(np.uint8)[:,None]
    k=int(np.clip(round(expected*0.24),5,45))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((k,1),np.uint8)).ravel().astype(bool)
    mask=cv2.morphologyEx(mask.astype(np.uint8)[:,None],cv2.MORPH_OPEN,
                           np.ones((3,1),np.uint8)).ravel().astype(bool)
    candidates=[]
    for a,b in _runs(mask):
        length=b-a
        # Broad optical width filter, not a measure of diameter.
        if not (0.55*expected<=length<=1.6*expected):continue
        if a<7 or b>len(mask)-7:continue
        center=y0+(a+b-1)/2
        cost=abs(length-expected)/max(1,expected)
        if previous_y is not None:cost+=0.30*abs(center-previous_y)/max(1,expected)
        candidates.append((cost,a,b))
    if not candidates:
        raise ValueError(f'No continuous foreground silhouette ({len(_runs(mask))} difference bands; threshold={threshold:.1f}). Check ROI/background.')
    candidates.sort()
    _,a,b=candidates[0]
    # Compute binary mask inside search ROI; morphology bridges internal
    # specular stripes while preserving two physical EXTERIOR boundaries.
    binary=(diff>max(9.,threshold*0.8)).astype(np.uint8)
    vertical_k=int(np.clip(round(expected*0.24),7,45))
    binary=cv2.morphologyEx(binary,cv2.MORPH_CLOSE,np.ones((vertical_k,1),np.uint8))
    binary=cv2.morphologyEx(binary,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    height,width=diff.shape
    guides=np.full((width,2),np.nan,dtype=np.float64)
    coarse_center=(a+b-1)*0.5
    deviation=max(7,int(round(expected*0.23)))
    # Start each column from coherent central region. Hardware edges which are
    # spatially disconnected from the chosen stripe are never used as limits.
    for xi in range(width):
        line=binary[:,xi].astype(bool)
        segments=_runs(line)
        matching=[]
        for aa,bb in segments:
            center=(aa+bb-1)*0.5
            if aa<=coarse_center<=bb or (abs(center-coarse_center)<=0.24*expected):
                if abs(aa-a)<=deviation and abs((bb-1)-(b-1))<=deviation:
                    matching.append((abs(center-coarse_center),aa,bb))
        if matching:
            matching.sort()
            _,aa,bb=matching[0]
            guides[xi]=[float(y0+aa),float(y0+bb-1)]
    ok=np.all(np.isfinite(guides),axis=1)
    if ok.mean()<0.65:
        raise ValueError(f'Only {ok.sum()}/{width} coherent silhouette columns; background subtraction unreliable')
    # Median smoother affects only COARSE edge guidance, not sub-pixel output.
    for col in (0,1):
        values=guides[:,col].copy()
        replacement=float(np.median(values[ok]))
        filled=np.where(ok,values,replacement)
        smooth=cv2.medianBlur(filled.astype(np.float32)[None,:],5).ravel()
        guides[ok,col]=smooth[ok]
    pad=int(np.clip(round(expected*0.22),13,32))
    # v14.2.1: the global min/max of all column guides used to reject an
    # otherwise valid view when just ONE column was shifted by a glare/halo.
    # Construct a padded ROI using robust spatial percentiles; then enforce
    # the exterior-background requirement PER COLUMN. Columns without enough
    # exterior background are rejected, not used to force the measurement.
    good_top=guides[ok,0]
    good_bottom=guides[ok,1]
    robust_top=float(np.percentile(good_top,5))
    robust_bottom=float(np.percentile(good_bottom,95))
    proposed_top=min(float(y0+a),robust_top)
    proposed_bottom=max(float(y0+b-1),robust_bottom)
    roi=[x0,max(y0,int(np.floor(proposed_top-pad))),
         x1,min(y1,int(np.ceil(proposed_bottom+pad)))]
    # _half_crossing() requires at least 10 actual grayscale samples on each
    # side. Do not relax this requirement to make failed edges appear valid.
    min_exterior=11.0
    has_exterior=ok & ((guides[:,0]-roi[1])>=min_exterior) & \
                     ((roi[3]-guides[:,1])>=min_exterior)
    min_cols=max(12,int(np.ceil(0.65*width)))
    if int(has_exterior.sum())<min_cols:
        raise ValueError(
            f'Only {int(has_exterior.sum())}/{width} columns have enough exterior '
            'background (need >=65%). Expand/reposition this mirror SEARCH zone, '
            'or inspect the silhouette mask for incorrect foreground regions.'
        )
    # Invalidation is expressed explicitly as NaN so these columns cannot
    # silently participate in the downstream 50%-crossing and scale estimate.
    guides[~has_exterior]=np.nan
    coverage=float(np.mean((diff[a:b,:]>threshold)))
    return {'measurement_roi_xyxy':roi,'search_xyxy':list(search),'foreground_rough_edge_y':[y0+a,y0+b-1],
            'center_y':float(y0+coarse_center),'coarse_width_px':float(b-a),
            'adaptive_threshold_gray':float(threshold),'foreground_contrast':float(np.median(row_score[a:b])),
            'foreground_column_coverage':coverage,'candidates':len(candidates),
            'guides_working':guides,'valid_guide_columns':int(has_exterior.sum()),
            'mask_for_diagnostics':binary,'difference_for_diagnostics':diff}


def measure_silhouette(current_working,raw_shape,found,view):
    roi=list(found['measurement_roi_xyxy'])
    x0,y0,x1,y1=roi
    patch=current_working[y0:y1+1,x0:x1+1]
    gray=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)
    h,w=gray.shape
    guides=found['guides_working']
    top=np.full(w,np.nan); bottom=np.full(w,np.nan)
    topinfo={};valid=np.zeros(w,dtype=bool)
    widths=np.full(w,np.nan)
    for xi in range(w):
        if not np.all(np.isfinite(guides[xi])):continue
        ty,by=guides[xi]-y0
        if by-ty<9:continue
        f=gray[:,xi].astype(float)
        upper=_half_crossing(f,ty,inside_below=True,max_shift=5)
        lower=_half_crossing(f,by,inside_below=False,max_shift=5)
        if upper is None or lower is None:continue
        t,b=upper[0],lower[0]
        if b<=t or abs((b-t)-(by-ty))>max(7,(by-ty)*0.16):continue
        top[xi],bottom[xi]=t,b
        widths[xi]=b-t
        valid[xi]=True
        topinfo[xi]={'i_edge_top':upper[1],'i_edge_bottom':lower[1],
                     'i_bg_top':upper[2],'i_rod_top':upper[3],
                     'i_bg_bottom':lower[2],'i_rod_bottom':lower[3]}
    min_required=max(12,int(round(w*0.65)))
    if int(valid.sum())<min_required:
        raise ValueError(f'Silhouette found but original-image 50% crossings valid in only {int(valid.sum())}/{w} columns. Verify true contours/reflection.')
    # Reject local discontinuities on actual subpixel edges; no interpolation
    # or smoothing is applied to the metrology values.
    for arr in (top,bottom):
        med=float(np.median(arr[valid]))
        good=valid & (np.abs(arr-med)<max(8,float(np.nanmedian(widths[valid]))*0.22))
        valid &= good
    if valid.sum()<min_required:
        raise ValueError('Silhouette edge positions are inconsistent between columns')
    raw_w,raw_h=int(raw_shape[1]),int(raw_shape[0])
    if (raw_w,raw_h)!=(current_working.shape[0],current_working.shape[1]):
        raise ValueError('Rotated orientation / camera resolution mismatch')
    vals=widths[valid]
    # Runtime engine interface compatible with existing top/bottom overlays.
    engine=SimpleNamespace(valid=valid,top_edges=top,bottom_edges=bottom,widths_px=widths,
       diameters_mm=None,rectified_gray=gray,intensity_crossing_by_col=topinfo,
       v142_mask=found['mask_for_diagnostics'],v142_diff=found['difference_for_diagnostics'],
       v142_guides=guides,v142_method='silhouette')
    physical=None;scale=None
    ref=view.get('local_scale_calibration')
    if ref:
        expected_dims=[int(raw_w),int(raw_h)]
        if ref.get('original_image_size_wh')==expected_dims:
            # Effective local scale valid only around the same optical zone.
            reference_center=ref['search_roi_center_xy']
            # v14.6.6 calibration-gate compatibility fix: a moving filament
            # can be anywhere inside a tall search ROI. Compare the ACTUAL
            # detected silhouette center, not the search-box midpoint.
            # Original edges, 50% crossing and metric conversion are unchanged.
            this_center=[0.5*(found['search_xyxy'][0]+found['search_xyxy'][2]),
                         float(found.get('center_y',0.5*(found['search_xyxy'][1]+found['search_xyxy'][3])))]
            if abs(this_center[0]-reference_center[0])<=20 and abs(this_center[1]-reference_center[1])<=20:
                scale=float(ref['mm_per_px'])
                physical=robust_summary(vals*scale)
    score=float(np.clip(0.6*valid.mean()+0.4*min(1.,found['foreground_contrast']/max(12,2*found['adaptive_threshold_gray'])),0,1))
    meas={'valid_slices':int(valid.sum()),'total_slices':w,'pixel_stats':robust_summary(vals),
          'physical_stats':physical,'calibration_status':('LOCAL SCALE / reference only' if physical else 'UNCALIBRATED (pixel only)'),
          'raw_homography_stats':None,'K_ref':None,'scale_mm_per_px':scale,
          'roi':roi,'mode':'measure','detection_method':'silhouette',
          'outer_edge_constraint':True,'confidence_heuristic':score,
          'note':'Silhouette + ORIGINAL intensity 50% crossing; mirror local scale is not a homography.'}
    return meas,engine


def process_silhouette_view(current_working,background_working,raw_shape,view):
    found=locate_silhouette(current_working,background_working,view['search_roi_xyxy'],
                          view['approx_expected_width_px'],view['foreground_threshold'],view.get('previous_y'))
    meas,engine=measure_silhouette(current_working,raw_shape,found,view)
    return found,meas,engine

"""v14.1 outer silhouette guidance and post-measurement validation.

No subpixel measurement occurs here. A registered empty-scene image supplies an
*approximate* foreground silhouette, which constrains the original v11 peak
selection.  The 50% intensity crossing is still performed on the untouched
current image (or its polarity inverse for dark foreground).

Silhouette != ground truth. Severe reflections, misregistration and saturation
can still invalidate an apparently coherent contour.
"""
from __future__ import annotations
import numpy as np
import cv2


def estimate_outer_silhouette_guides(current_working, background_working,
                                     roi, rough_edges, *, polarity, radius_px=8.0):
    x0, y0, x1, y1 = [int(v) for v in roi]
    top, bottom = map(int, rough_edges)
    if top >= bottom or not (y0 < top < bottom < y1):
        raise ValueError('Rough silhouette touches dynamic ROI boundary')
    if current_working.shape != background_working.shape:
        raise ValueError('Images do not match')
    if polarity not in ('bright','dark'):
        raise ValueError('Unknown foreground polarity')
    factor = 1 if polarity == 'bright' else -1
    current = cv2.cvtColor(current_working[y0:y1+1,x0:x1+1], cv2.COLOR_BGR2GRAY)
    background = cv2.cvtColor(background_working[y0:y1+1,x0:x1+1], cv2.COLOR_BGR2GRAY)
    delta = factor * (current.astype(np.float32) - background.astype(np.float32))
    delta = cv2.GaussianBlur(delta, (1,3), 0.0)
    w=delta.shape[1];h=delta.shape[0]
    ta,ba=top-y0,bottom-y0
    # Use regions OUTSIDE the coarse band to estimate changing illumination;
    # do not let the interior filament/reflections set the noise baseline.
    exterior = np.r_[delta[:max(1, ta-3), :], delta[min(h,ba+4):, :]]
    if exterior.shape[0]<3:
        raise ValueError('Insufficient outside-background pixels for silhouette guide')
    baseline = np.median(exterior, axis=0)
    r=max(3.0,float(radius_px))
    guides=np.tile([float(top),float(bottom)],(w,1))
    located=0
    for xi in range(w):
        signal=delta[:,xi]-baseline[xi]
        inner=signal[max(0,ta+4):min(h,ba-3)]
        if inner.size<5:
            continue
        strength=float(np.median(inner))
        # Foreground has to be coherent across its INTERIOR; if not, leave
        # the robust row-median rough band as the guide, and count fallback.
        if strength<10:
            continue
        level=max(9.0,min(35.0,0.20*strength))
        win=max(5,int(round(r*1.5)))
        # Follow the ONE foreground component containing the filament centre.
        # Taking the last changed pixel would incorrectly include brackets or
        # mirror seams sitting just outside the actual contour.
        mask=(signal>level).astype(np.uint8)
        mask=cv2.morphologyEx(mask[:,None],cv2.MORPH_CLOSE,
                              np.ones((5,1),np.uint8)).ravel().astype(bool)
        centre=(ta+ba)//2
        seeds=np.flatnonzero(mask[max(0,centre-3):min(h,centre+4)])
        if not len(seeds):
            continue
        seed=int(max(0,centre-3)+seeds[np.argmin(np.abs(seeds + max(0,centre-3)-centre))])
        start=seed
        while start>0 and mask[start-1]:
            start-=1
        end=seed
        while end<h-1 and mask[end+1]:
            end+=1
        yt=float(y0+start)
        yb=float(y0+end)
        # Reject implausible local silhouettes, not change the original edge
        # width or 'fix' an uncertain reading to 1.75 mm.
        if yt >= yb-8 or abs(yt-top)>win or abs(yb-bottom)>win:
            continue
        guides[xi]=[yt,yb]
        located+=1
    if located / max(1,w) < 0.65:
        raise ValueError(f'Only {located}/{w} columns have a credible outer silhouette; check alignment and reflections')
    return {'guides':guides,'rough_edges_y': [top,bottom],
            'silhouette_column_fraction': float(located/max(1,w)),
            'source': 'registered current-minus-background; location only',
            'roi':list(roi)}


def edge_alignment_quality(engine, roi, guide_data, radius):
    """Report constrained edge agreement; does not certify physical accuracy."""
    valid = np.asarray(engine.valid,dtype=bool)
    g=guide_data['guides']
    n=len(valid)
    if g.shape!=(n,2):
        raise ValueError('Silhouette guide and engine slice counts disagree')
    if valid.sum()<max(10,int(0.75*n)):
        return {'passed':False,'reason':'too few edge-constrained slices'}
    top = np.asarray(engine.top_edges)[valid] + roi[1]
    bottom = np.asarray(engine.bottom_edges)[valid] + roi[1]
    discrepancy = np.column_stack([top,bottom]) - g[valid]
    median_abs = float(np.median(np.abs(discrepancy)))
    measured_width = float(np.median(bottom-top))
    rough_width = float(np.median(g[valid,1]-g[valid,0]))
    tol=max(3.,0.85*float(radius))
    if median_abs > tol:
        return {'passed':False, 'reason':f'median edge displacement {median_abs:.1f}px exceeds {tol:.1f}px'}
    if not 0.80*rough_width <= measured_width <= 1.20*rough_width:
        return {'passed':False,'reason':f'width {measured_width:.1f}px inconsistent with coarse silhouette {rough_width:.1f}px'}
    return {'passed':True,'median_abs_displacement_px':median_abs,
            'coarse_width_px':rough_width,'measured_width_px':measured_width,
            'guided_column_fraction':guide_data['silhouette_column_fraction'],
            'reason':'Constrained edges agree with background silhouette (not absolute accuracy)'}

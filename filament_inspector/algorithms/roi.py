"""v14.6 fixed 40-column ROI geometry, in 90° clockwise full-resolution coordinates.

The geometrical center of 4608x2592 native sensor after CW rotation is x=1295.5,
but hardware results identified a better-aligned physical filament band near x=1350.
This target location remains operator-adjustable. Inclusive x0/x1 semantics apply.
"""
from __future__ import annotations


def fixed_x_band(shape, width_px=40, center_x=None):
    """Return exactly N inclusive native working-image columns around a chosen X."""
    height, width = shape[:2]
    count = int(width_px)
    if count < 12 or count > width:
        raise ValueError(f'ROI width {count} invalid for working width {width}')
    center = (width - 1) / 2 if center_x is None else float(center_x)
    if not center == center or center == float('inf') or center == float('-inf'):
        raise ValueError('ROI center X must be finite')
    x0 = int(round(center - (count - 1) / 2))
    x1 = x0 + count - 1
    if x0 < 0 or x1 >= width:
        raise ValueError(f'ROI x={x0}..{x1} falls outside working image width {width}')
    return x0, x1


# Compatibility alias for draft v14.6.
image_center_band = fixed_x_band


def centered_roi(shape, y0, y1, width_px=40, min_height=30, center_x=None):
    """Lock X and width; retain the selected vertical Direct/Mirror band."""
    height, _ = shape[:2]
    lo, hi = sorted((int(round(y0)), int(round(y1))))
    if lo < 0 or hi >= height or hi - lo + 1 < min_height:
        raise ValueError(f'Select at least {min_height} vertical pixels inside the image')
    x0, x1 = fixed_x_band(shape, width_px, center_x)
    return [x0, lo, x1, hi]


def centered_all_views(shape, rois, width_px=40, center_x=None):
    """Apply identical full-res X band, without changing each view's Y band."""
    out = {}
    for key in ('direct', 'left', 'right'):
        r = rois.get(key)
        out[key] = (None if r is None else centered_roi(
            shape, r[1], r[3], width_px, center_x=center_x))
    return out


def centered_description(shape, width_px=40, center_x=None):
    x0, x1 = fixed_x_band(shape, width_px, center_x)
    selected = (shape[1]-1)/2 if center_x is None else float(center_x)
    return (f'ROI X center={selected:g} (image midpoint={(shape[1]-1)/2:g}) '
            f'| X={x0}..{x1} | {x1-x0+1} full-res columns')

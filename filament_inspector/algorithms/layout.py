"""Coordinate-only, testable ROI layout helpers for three-view v14.

All rectangles use inclusive xyxy coordinates of the 90-degree CW working image.
No metrology or photometric modification is performed here.
"""
from __future__ import annotations


def _valid(box, shape, min_w=12, min_h=30):
    height, width = shape[:2]
    x0, y0, x1, y1 = [int(v) for v in box]
    if not (0 <= x0 < x1 < width and 0 <= y0 < y1 < height):
        raise ValueError(f"Search rectangle {list(box)} lies outside the working image")
    if (x1 - x0 + 1) < min_w or (y1 - y0 + 1) < min_h:
        raise ValueError(f"Search rectangle needs at least {min_w} columns and {min_h} rows")
    return [x0, y0, x1, y1]


def normalize_drag(a, b, shape):
    """Convert any two clicked working coordinates to an inclusive, valid ROI."""
    h, w = shape[:2]
    x0, x1 = sorted((int(round(a[0])), int(round(b[0]))))
    y0, y1 = sorted((int(round(a[1])), int(round(b[1]))))
    return _valid([max(0, x0), max(0, y0), min(w-1, x1), min(h-1, y1)], shape)


def move_roi_vertical(box, desired_delta, shape):
    """Move only Y while preserving exact X coordinates AND rectangle height."""
    x0, y0, x1, y1 = _valid(box, shape)
    height = shape[0]
    dy = int(round(desired_delta))
    dy = max(-y0, min(height-1-y1, dy))
    return [x0, y0+dy, x1, y1+dy]


def auto_generate_mirrors(direct, shape, gap_ratio=1.0):
    """Propose symmetric coarse search zones without claiming optical registration.

    The mirror ROIs share the DIRECT x interval and height; only Y differs.
    Users must visually adjust mirror positions before fine localization.
    """
    x0, y0, x1, y1 = _valid(direct, shape)
    hh = y1-y0+1
    shift = max(30, int(round(hh*float(gap_ratio))))
    if shift <= 0:
        raise ValueError("Mirror offset must be positive")
    above = move_roi_vertical(direct, -shift, shape)
    below = move_roi_vertical(direct, +shift, shape)
    if above == direct or below == direct:
        raise ValueError("Insufficient vertical image space to place both mirror search ROIs")
    return {'direct':list(direct),'left':above,'right':below}


def validate_shared_x(rois, shape):
    direct = _valid(rois['direct'],shape)
    if rois.get('left') is None or rois.get('right') is None:
        raise ValueError('Both mirror rectangles are required')
    for key in ('left','right'):
        r=_valid(rois[key],shape)
        if (r[0],r[2]) != (direct[0],direct[2]):
            raise ValueError(f'{key} mirror horizontal position must match Direct View')
    return True

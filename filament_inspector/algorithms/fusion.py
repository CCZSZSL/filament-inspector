"""Virtual F-Direct diameter derived from TWO independently calibrated mirror views.

There is NO physical measurement at the direct optical view. Never average pixel
widths or silently re-use the Direct H x K_ref correction for mirror optics.
"""
from __future__ import annotations
import math

class MirrorFusionError(ValueError):
    pass


def fuse_mirror_views(results):
    """Compute (Left_mm + Right_mm)/2 from valid per-view 40-column fusions.

    Returns a new independent record; does not overwrite left/right measurements.
    No claim of sub-0.01 mm accuracy, physical roundness or a third view.
    """
    verified={}
    for key in ('left','right'):
        m=results.get(key)
        if not isinstance(m,dict):
            raise MirrorFusionError(f'{key.title()} Mirror: no valid result this frame')
        f=m.get('fusion') or {}
        if f.get('unit')!='mm' or m.get('physical_stats') is None:
            raise MirrorFusionError(f'{key.title()} Mirror: independent local mm/px calibration required (currently pixel only)')
        diameter=float(f.get('value',float('nan')))
        if not math.isfinite(diameter) or diameter<=0:
            raise MirrorFusionError(f'{key.title()} Mirror: invalid calibrated diameter')
        if int(f.get('valid_columns',0))<26:
            raise MirrorFusionError(f'{key.title()} Mirror: insufficient valid columns')
        scale=float(m.get('scale_mm_per_px') or float('nan'))
        if not math.isfinite(scale) or scale<=0:
            raise MirrorFusionError(f'{key.title()} Mirror: missing valid independent scale')
        verified[key]=(diameter,m,f)
    left=verified['left'][0]
    right=verified['right'][0]
    return {
        'name':'F-Direct View',
        'type':'virtual_two_mirror_mean',
        'physical_direct_view_measured':False,
        'formula':'(D_left_mm + D_right_mm) / 2',
        'value':(left+right)*.5,
        'unit':'mm',
        'left_mm':left,
        'right_mm':right,
        'absolute_difference_mm':abs(left-right),
        'valid_columns_left':int(verified['left'][2]['valid_columns']),
        'valid_columns_right':int(verified['right'][2]['valid_columns']),
        'calibration_status':'TWO INDEPENDENT MIRROR LOCAL SCALES',
        'roundness':'NOT MEASURED',
        'accuracy_validated':False,
        'note':'Mean of two distinct projected diameters, not a real third view. '
               'Nominal-filament reference calibrations are provisional; ±0.01 mm NOT verified.'
    }

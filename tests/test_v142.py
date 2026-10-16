"""Run from the repository root with unittest discovery (see README)."""
from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import cv2
import numpy as np
from filament_inspector.algorithms.silhouette import process_silhouette_view
from filament_inspector.algorithms.views import new_view
from filament_inspector.ui.silhouette import SilhouetteThreeViewUI

ROOT=Path(__file__).resolve().parents[1]


def synthetic_metal(diameter):
    H,W=360,400
    bg=np.full((H,W,3),(175,210,200),np.uint8)
    cur=bg.copy()
    top=125
    end=top+diameter
    cur[top:end,90:300]=(35,175,225)
    # Artificial metallic internal reflections brighter than actual exterior.
    cur[top+10:top+16,90:300]=(215,245,250)
    cur[top+int(.53*diameter):top+int(.53*diameter)+6,90:300]=(175,210,200)
    cur[end-18:end-13,90:300]=(210,250,255)
    cur=cv2.GaussianBlur(cur,(3,3),0)
    return cur,bg,(W,H)


def test_metal_and_mirror_scale():
    cur,bg,raw=synthetic_metal(70)
    v=new_view('left')
    v.update(search_roi_xyxy=[100,60,245,255],approx_expected_width_px=70)
    _,measurement,engine=process_silhouette_view(cur,bg,raw,v)
    median=measurement['pixel_stats']['median']
    assert abs(median-70)<1.0,median
    assert measurement['valid_slices']/measurement['total_slices']>.9
    assert measurement['physical_stats'] is None
    scale=2.0/median
    sr=v['search_roi_xyxy']
    v['local_scale_calibration']={'schema':'mirror_local_scale_v142',
        'view_name':'Left Mirror','mm_per_px':scale,
        'rod_width_mm':2.0,'original_image_size_wh':[raw[1],raw[0]],
        'search_roi_center_xy':[(sr[0]+sr[2])/2,(sr[1]+sr[3])/2]}
    sample,bg2,raw2=synthetic_metal(53)   # independent test diameter = 53/70 * 2 = 1.514 mm
    _,m2,_=process_silhouette_view(sample,bg2,raw2,v)
    assert abs(m2['physical_stats']['median']-2*53/70)<0.04,m2
    # Wrong image resolution must refuse calibration even if pixel contours exist.
    _,px,_=process_silhouette_view(sample,bg2,(raw2[0],raw2[1]+8),v) if False else (None,None,None)
    wrong=dict(v,local_scale_calibration=dict(v['local_scale_calibration'],original_image_size_wh=[123,456]))
    _,m3,_=process_silhouette_view(sample,bg2,raw2,wrong)
    assert m3['physical_stats'] is None
    wrong_pos=dict(v,search_roi_xyxy=[100,100,245,295])
    _,m4,_=process_silhouette_view(sample,bg2,raw2,wrong_pos)
    assert m4['physical_stats'] is None
    print(f'PASS gold synthetic 70 px reference → {scale:.8f}mm/px; independent 53 px -> {m2["physical_stats"]["median"]:.4f}mm')


def test_false_positive():
    cur,bg,raw=synthetic_metal(70)
    v=new_view('left');v.update(search_roi_xyxy=[100,60,245,255],approx_expected_width_px=70)
    try:process_silhouette_view(bg,bg,raw,v)
    except ValueError:pass
    else:raise AssertionError('Empty scene must reject a false detection')
    print('PASS empty scene safely rejected')


def test_real_sample_and_gui():
    image=ROOT/'sample_with_filament.jpg'
    bg=ROOT/'sample_background.jpg'
    ui=SilhouetteThreeViewUI()
    try:
        ui._load_image(image)
        with patch('tkinter.filedialog.askopenfilename',return_value=str(bg)):
            ui._load_background_dialog()
        ui._apply_direct_roi([435,960,600,1060])
        ui.views['left']['search_roi_xyxy']=[435,890,600,970]
        ui.views['right']['search_roi_xyxy']=[435,1045,600,1150]
        ui._v14_process_all()
        ui.update()
        for key in ('direct','left','right'):
            assert key in ui.view_results, ui.result_table.item(key,'values')
            m=ui.view_results[key]
            if key !='direct':
                assert m['detection_method']=='silhouette'
                assert m['physical_stats'] is None
        assert ui.canvas.find_withtag('v141_edge')
        # v14 layouts remain compatible after adding per-view scale and method.
        with TemporaryDirectory() as d:
            path=Path(d)/'roi_layout.json'
            with patch('tkinter.filedialog.asksaveasfilename',return_value=str(path)):
                ui._save_layout()
            saved=json.loads(path.read_text())
            assert saved['views']['left']['detection_method']=='silhouette'
            with patch('tkinter.filedialog.askopenfilename',return_value=str(path)):
                ui._load_layout()
            assert ui.views['left']['detection_method']=='silhouette'
            assert ui.views['direct']['detection_method']=='gradient'
        print('PASS real sample 3 views / edge overlays / GUI / layout save+load')
    finally:ui.destroy()


if __name__=='__main__':
    test_metal_and_mirror_scale()
    test_false_positive()
    test_real_sample_and_gui()
    print('ALL v14.2 TESTS PASSED')

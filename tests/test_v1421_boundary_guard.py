"""Regression for v14.2 false failure from a handful of halo-affected columns.

Run from the repository root: python3 -m unittest discover -s tests -p test_v1421_boundary_guard.py
"""
from test_v142 import synthetic_metal
from filament_silhouette_v142 import process_silhouette_view
from filament_three_view_core_v13 import new_view


def test_sparse_reflection_halo():
    cur, bg, raw_shape = synthetic_metal(70)
    view = new_view('right')
    view.update(search_roi_xyxy=[100, 60, 245, 255], approx_expected_width_px=70)
    for extension in (0, 6, 9, 12, 15):
        noisy = cur.copy()
        if extension:
            # Only 20/146 columns receive an exterior specular halo.
            # Old v14.2 rejected all 146 columns at once even though the
            # measurement ROI was otherwise well lit and properly sized.
            noisy[125-extension:125, 109:129] = noisy[126, 109:129]
        found, measurement, _ = process_silhouette_view(noisy, bg, raw_shape, view)
        assert measurement['valid_slices'] >= 0.85*measurement['total_slices']
        assert abs(measurement['pixel_stats']['median'] - 70.0) < 0.5
        assert found['valid_guide_columns'] >= 0.85*measurement['total_slices']
    print('PASS sparse reflection halo: no whole-view rejection; 70px width retained')


def test_insufficient_outer_background_still_rejects():
    cur, bg, raw_shape = synthetic_metal(70)
    view = new_view('right')
    view.update(search_roi_xyxy=[100, 120, 245, 208], approx_expected_width_px=70)
    try:
        process_silhouette_view(cur, bg, raw_shape, view)
    except ValueError:
        print('PASS inadequate SEARCH exterior background is still rejected')
        return
    raise AssertionError('Should reject when there is no exterior background')


if __name__ == '__main__':
    test_sparse_reflection_halo()
    test_insufficient_outer_background_still_rejects()

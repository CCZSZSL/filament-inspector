"""Fast, offline synthetic validation; not a camera-hardware accuracy test."""
import unittest
import numpy as np
import cv2
from filament_seed_guided_v1462 import (
    SCHEMA,SeedDetectionError,sample_record,build_model,measure_seeded,locate_seeded,validate_model,
)
from filament_three_view_core_v13 import new_view

class SyntheticSeedTests(unittest.TestCase):
    def setUp(self):
        # Working H=200, W=96; if full-res native were used, dimensions swap.
        self.height,self.width=210,96
        self.bg=np.full((self.height,self.width),112,np.uint8)
        self.cur=self.bg.copy()
        self.start,self.end=69,131  # 62-64-pixel silhouette
        self.cur[self.start:self.end,:]=65
        # bright streak and local partial bright material, opposite polarities
        self.cur[88:105,:]=168
        self.cur[116:124,:]=142
        self.cur=cv2.GaussianBlur(self.cur,(5,5),0)
        self.cur=cv2.cvtColor(self.cur,cv2.COLOR_GRAY2BGR)
        self.bg=cv2.cvtColor(self.bg,cv2.COLOR_GRAY2BGR)
        self.roi=[20,27,59,169] # exactly 40 source columns
        self.view=new_view('direct');self.view['search_roi_xyxy']=self.roi
        self.view['approx_expected_width_px']=62
        self.view['foreground_threshold']=11
        samples=[sample_record(self.cur,[25,76,38,83],self.roi),
                 sample_record(self.cur,[40,91,51,98],self.roi),
                 sample_record(self.cur,[26,118,37,121],self.roi)]
        self.view['seed_gray_model']=build_model(samples,image_shape=(self.height,self.width))

    def test_one_sample_validates_and_measures(self):
        # One interior seed in a dark patch must be allowed even with two
        # bright interior reflection bands and mixed edge polarities.
        patch=sample_record(self.cur,[25,76,38,83],self.roi)
        self.view['seed_gray_model']=build_model([patch],image_shape=(self.height,self.width))
        found,m,e=measure_seeded(self.cur,self.bg,(self.width,self.height),self.view)
        self.assertEqual(len(self.view['seed_gray_model']['samples']),1)
        self.assertEqual(m['total_slices'],40)
        self.assertGreaterEqual(m['valid_slices'],26)
        self.assertTrue(np.isfinite(m['pixel_stats']['median']))

    def test_histograms_and_interior(self):
        model=self.view['seed_gray_model']
        self.assertEqual(model['schema'],SCHEMA)
        self.assertEqual(len(model['samples']),3)
        self.assertEqual(len(model['histogram_256_bins']),256)
        self.assertEqual(sum(model['histogram_256_bins']),sum(s['pixel_count'] for s in model['samples']))
        with self.assertRaises(ValueError):sample_record(self.cur,[21,76,28,83],self.roi)

    def test_mixed_contrast_subpixel_and_40_columns(self):
        found,m,e=measure_seeded(self.cur,self.bg,(self.width,self.height),self.view)
        self.assertEqual(m['total_slices'],40)
        self.assertGreaterEqual(m['valid_slices'],26)
        self.assertGreater(m['pixel_stats']['median'],58)
        self.assertLess(m['pixel_stats']['median'],66)
        self.assertIsNone(m['physical_stats'])
        self.assertEqual(m['detection_method'],'seed_guided')
        self.assertTrue(np.all(np.isfinite(e.top_edges[e.valid])))
        self.assertTrue(np.all(np.isfinite(e.bottom_edges[e.valid])))
        self.assertLess(abs(found['center_y']-100),5)

    def test_no_foreground_diagnostic(self):
        with self.assertRaises(SeedDetectionError) as exc:
            measure_seeded(self.bg,self.bg,(self.width,self.height),self.view)
        self.assertIsNotNone(exc.exception.diagnostic)
        self.assertIn('foreground_mask',exc.exception.diagnostic)

    def test_direct_h_and_kref_only_on_matching_sensor(self):
        profile={'local_planar_calibration':{
            'original_image_width_px':self.height,
            'original_image_height_px':self.width,
            'homography_calibration_view_px_to_mm':[[0.029,0,0],[0,0.029,0],[0,0,1]]},
            'target_plane_reference_correction':{'enabled':True,'correction_factor_K_ref':1.012}}
        self.view['profile']=profile
        found,m,e=measure_seeded(self.cur,self.bg,(self.width,self.height),self.view)
        self.assertIsNotNone(m['physical_stats'])
        self.assertAlmostEqual(m['physical_stats']['median'],
                               m['pixel_stats']['median']*0.029*1.012,places=5)
        self.assertEqual(m['K_ref'],1.012)
        with self.assertRaises(SeedDetectionError):
            measure_seeded(self.cur,self.bg,(999,999),self.view)

    def test_mirror_local_scale_maintained(self):
        from filament_three_view_core_v13 import VIEW_LABELS
        self.view['name']='left'
        self.view['local_scale_calibration']={
            'original_image_size_wh':[self.height,self.width],
            'search_roi_center_xy':[(self.roi[0]+self.roi[2])/2,(self.roi[1]+self.roi[3])/2],
            'mm_per_px':0.0279}
        found,m,e=measure_seeded(self.cur,self.bg,(self.width,self.height),self.view)
        self.assertIsNotNone(m['physical_stats'])
        self.assertAlmostEqual(m['physical_stats']['median'],
                               m['pixel_stats']['median']*0.0279,places=5)
        self.assertIn('LOCAL SCALE',m['calibration_status'])

    def test_changed_capture_rejected(self):
        model=self.view['seed_gray_model']
        model['acquisition']={'focus_epoch':'3','exposure_us':'1000'}
        with self.assertRaises(ValueError):
            validate_model(model,(self.height,self.width),self.roi,{'focus_epoch':'4','exposure_us':'1000'})

    def test_roi_movement_rejects_seeds(self):
        with self.assertRaises(ValueError):
            validate_model(self.view['seed_gray_model'],(self.height,self.width),[20,135,59,200])

if __name__=='__main__':unittest.main(verbosity=2)

"""Synthetic tests; NOT real-camera validation of ±0.01mm accuracy."""
import unittest
import numpy as np
import cv2
from filament_three_view_core_v13 import new_view
from filament_seed_guided_v1462 import sample_record,build_model,SeedDetectionError
from filament_dynamic_fusion_v1463 import locate_dynamic,measure_dynamic

class DynamicFusionTests(unittest.TestCase):
    def setUp(self):
        self.h=225;self.w=95
        self.bg=np.full((self.h,self.w),114,np.uint8)
        self.box=[20,12,59,208]
        self.view=new_view('direct')
        self.view.update(search_roi_xyxy=self.box, approx_expected_width_px=62.,foreground_threshold=11.)
        cur=self.frame()
        sample=sample_record(cur,[26,84,40,92],self.box)
        self.view['seed_gray_model']=build_model([sample],image_shape=(self.h,self.w))
        self.background=cv2.cvtColor(self.bg,cv2.COLOR_GRAY2BGR)
    def frame(self,offset=0,angle=0):
        raw=self.bg.copy()
        for x in range(self.w):
            y0=int(round(72+offset+angle*(x-38)))
            raw[y0:y0+62,x]=58
            raw[y0+18:y0+28,x]=156
            raw[y0+45:y0+49,x]=139
        return cv2.cvtColor(cv2.GaussianBlur(raw,(5,5),0),cv2.COLOR_GRAY2BGR)
    def _measure(self,offset=0,angle=0):
        return measure_dynamic(self.frame(offset,angle),self.background,(self.w,self.h),self.view)
    def test_single_seed_moving_beyond_its_original_y(self):
        f0,m0,e0=self._measure()
        self.assertEqual(m0['fusion']['total_columns'],40)
        self.assertEqual(m0['fusion']['unit'],'px')
        self.assertGreaterEqual(m0['fusion']['valid_columns'],26)
        self.view['previous_y']=f0['center_y']
        for displacement in [18,32,-23,-32]:
            f,m,e=self._measure(displacement)
            self.assertAlmostEqual(f['center_y']-f0['center_y'],displacement,delta=2)
            self.assertGreaterEqual(m['fusion']['valid_columns'],26)
            self.assertLess(abs(m['fusion']['value']-m0['fusion']['value']),1.0)
    def test_slope_corrected_normal_fusion(self):
        f,m,e=self._measure(angle=.12)
        self.assertGreaterEqual(m['fusion']['valid_columns'],26)
        self.assertLess(abs(m['fusion']['value']-62),2.)
        self.assertTrue(np.isfinite(m['fusion']['edge_fit_residual_median_px']))
    def test_direct_mm_not_invented_without_calibration(self):
        f,m,e=self._measure()
        self.assertIsNone(m['physical_stats'])
        self.assertEqual(m['fusion']['unit'],'px')
        self.assertFalse(m['fusion']['accuracy_validated'])
    def test_direct_h_and_kref(self):
        self.view['profile']={'local_planar_calibration':{
            'original_image_width_px':self.h,'original_image_height_px':self.w,
            'homography_calibration_view_px_to_mm':[[.028,0,0],[0,.028,0],[0,0,1]]},
            'target_plane_reference_correction':{'enabled':True,'correction_factor_K_ref':1.02}}
        f,m,e=self._measure()
        self.assertEqual(m['fusion']['unit'],'mm')
        self.assertAlmostEqual(m['fusion']['value'],m['pixel_stats']['median']*.028*1.02,delta=.02)
        self.assertFalse(m['fusion']['accuracy_validated'])
    def test_mirror_pixel_only_without_scale(self):
        self.view['name']='left'
        f,m,e=self._measure()
        self.assertEqual(m['fusion']['unit'],'px')
        self.assertIn('pixel',m['calibration_status'].lower())
    def test_blank_image_fails_cleanly(self):
        with self.assertRaises(SeedDetectionError) as ex:
            measure_dynamic(self.background,self.background,(self.w,self.h),self.view)
        self.assertIsNotNone(ex.exception.diagnostic)
    def test_old_seed_position_not_hard_gate(self):
        seed=self.view['seed_gray_model']['samples'][0]['rect_xyxy']
        self.assertLess(seed[3],100)
        f,m,e=self._measure(offset=32)
        self.assertGreater(f['foreground_rough_edge_y'][0],seed[3])
        self.assertGreaterEqual(m['fusion']['valid_columns'],26)
    def test_no_dimensions_to_mm_from_uncalibrated_mirror(self):
        self.view['name']='right'
        self.view['local_scale_calibration']={
            'original_image_size_wh':[self.h,self.w],
            'search_roi_center_xy':[(self.box[0]+self.box[2])/2,103],
            'mm_per_px':.029}
        f,m,e=self._measure(offset=0)
        self.assertEqual(m['fusion']['unit'],'mm')
        f,m,e=self._measure(offset=30)
        self.assertEqual(m['fusion']['unit'],'px')
        self.assertIsNone(m['physical_stats'])

if __name__=='__main__':unittest.main(verbosity=2)

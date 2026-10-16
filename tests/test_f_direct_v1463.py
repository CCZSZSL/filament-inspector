"""Offline tests of the v14.6.3-F virtual fused mirror output."""
import unittest,cv2,numpy as np
from unittest.mock import patch
from filament_inspector.algorithms.fusion import fuse_mirror_views,MirrorFusionError
from filament_inspector.algorithms.seed import sample_record,build_model
from filament_inspector.ui.fusion import FDirectTwoMirrorUI


def dummy(value,unit='mm',scale=.027):
    return {'physical_stats':{'mean':value} if unit=='mm' else None,
            'scale_mm_per_px':scale if unit=='mm' else None,
            'fusion':{'unit':unit,'value':value,'valid_columns':40}}

class PureFusionTests(unittest.TestCase):
    def test_mean_and_difference(self):
        r=fuse_mirror_views({'left':dummy(1.74),'right':dummy(1.76)})
        self.assertAlmostEqual(r['value'],1.75,places=12)
        self.assertAlmostEqual(r['absolute_difference_mm'],.02,places=12)
        self.assertFalse(r['physical_direct_view_measured'])
        self.assertFalse(r['accuracy_validated'])
    def test_no_mirror_pixel_average(self):
        with self.assertRaisesRegex(MirrorFusionError,'independent local'):
            fuse_mirror_views({'left':dummy(64,'px'),'right':dummy(62,'px')})
    def test_one_missing_rejects(self):
        with self.assertRaisesRegex(MirrorFusionError,'no valid'):
            fuse_mirror_views({'left':dummy(1.75)})
    def test_no_accidental_direct_dependency(self):
        r=fuse_mirror_views({'direct':dummy(9.0),'left':dummy(1.72),'right':dummy(1.78)})
        self.assertAlmostEqual(r['value'],1.75)

class GuiFusionTests(unittest.TestCase):
    @staticmethod
    def fill_view(app,key,current_gray,roi,start_y):
        view=app.views[key]
        view['search_roi_xyxy']=roi
        view['approx_expected_width_px']=62
        view['foreground_threshold']=11
        old=np.full(current_gray.shape,114,dtype=np.uint8)
        old[start_y:start_y+62]=58
        bgr=cv2.cvtColor(old,cv2.COLOR_GRAY2BGR)
        patch=sample_record(bgr,[25,start_y+9,39,start_y+18],roi)
        view['seed_gray_model']=build_model([patch],image_shape=current_gray.shape)
    def test_gui_mirror_only_and_no_false_mm(self):
        app=FDirectTwoMirrorUI()
        try:
            h,w=315,95
            bg=np.full((h,w),114,np.uint8)
            raw=bg.copy()
            raw[55:117,:]=58
            raw[67:77,:]=156
            raw[195:257,:]=58
            raw[207:217,:]=156
            cur=cv2.cvtColor(cv2.GaussianBlur(raw,(5,5),0),cv2.COLOR_GRAY2BGR)
            app.image_cv=cur
            app.background_cv=cv2.cvtColor(bg,cv2.COLOR_GRAY2BGR)
            app.image_cv_raw=np.zeros((w,h,3),dtype=np.uint8)
            self.fill_view(app,'left',raw,[20,5,59,145],55)
            self.fill_view(app,'right',raw,[20,145,59,304],195)
            app.process_all()
            self.assertNotIn('direct',app.view_results)
            self.assertNotIn('direct',app.engines)
            self.assertEqual(app.view_results['left']['fusion']['unit'],'px')
            self.assertEqual(app.view_results['right']['fusion']['unit'],'px')
            self.assertIsNone(app.f_direct_result)
            self.assertIn('NOT AVAILABLE',app.result_table.item('direct','values')[1])
            # Both mirrors now have their OWN scale, no Direct calibration needed.
            for n in ('left','right'):
                v=app.views[n]
                v['local_scale_calibration']={'original_image_size_wh':[h,w],
                    'mm_per_px':.028,'search_roi_center_xy':[(20+59)/2,86 if n=='left' else 226]}
            app.process_all()
            f=app.f_direct_result
            self.assertIsNotNone(f,str(app.detail_var.get()))
            self.assertEqual(f['unit'],'mm')
            self.assertEqual(app.result_table.item('direct','values')[0],'F-Direct View')
            self.assertAlmostEqual(f['value'],(f['left_mm']+f['right_mm'])/2)
            self.assertGreater(f['valid_columns_left'],25)
            # Bad mirror can't be mixed with stale result or silently averaged.
            app.views['right']['local_scale_calibration']=None
            app.process_all()
            self.assertIsNone(app.f_direct_result)
        finally:app.destroy()

    def test_provisional_mirror_calibration_on_dynamic_pixels(self):
        app=FDirectTwoMirrorUI()
        try:
            h,w=315,95
            bg=np.full((h,w),114,np.uint8)
            raw=bg.copy()
            raw[55:117,:]=58;raw[67:77,:]=156
            raw[195:257,:]=58;raw[207:217,:]=156
            app.image_cv=cv2.cvtColor(cv2.GaussianBlur(raw,(5,5),0),cv2.COLOR_GRAY2BGR)
            app.background_cv=cv2.cvtColor(bg,cv2.COLOR_GRAY2BGR)
            app.image_cv_raw=np.zeros((w,h,3),dtype=np.uint8)
            self.fill_view(app,'left',raw,[20,5,59,145],55)
            self.fill_view(app,'right',raw,[20,145,59,304],195)
            app.known_width_var.set(1.75)
            for n in ('left','right'):
                app._selected_name=n
                app._refresh_fields()
                with patch('filament_inspector.ui.fusion.messagebox.askyesno',return_value=True), \
                     patch('filament_inspector.ui.fusion.messagebox.showinfo'):
                    app._calibrate_mirror_scale()
                cal=app.views[n]['local_scale_calibration']
                self.assertEqual(cal['schema'],'mirror_local_scale_v142')
                self.assertEqual(cal['reference_type'],'provisional_working_reference')
                self.assertFalse(cal['accuracy_validated'])
            app.process_all()
            self.assertAlmostEqual(app.f_direct_result['value'],1.75,delta=.02)
        finally:app.destroy()

if __name__=='__main__':unittest.main(verbosity=2)

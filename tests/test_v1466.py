"""v14.6.6 offline regression / GUI tests. GUI requires DISPLAY or xvfb-run."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from filament_inspector.algorithms.fusion import fuse_mirror_views, MirrorFusionError
from filament_inspector.calibration.local import (
    CalibrationError, frame_record, aggregate_records, make_scale,
    validate_scale, FRAME_COUNT,
)
from filament_inspector.algorithms.views import new_view
from filament_inspector.ui.local_calibration import LocalCalibrationUI, validate_saved_state


def record(px=63.0,quality=.95,cols=40,center=100.,res=.25):
    return {'fused_width_px':float(px),'quality_score':quality,'valid_columns':cols,
            'tracked_center_y_px':center,'edge_fit_residual_px':res}


def view(n):
    v=new_view(n)
    v['search_roi_xyxy']=[1320,80 if n=='left' else 300,
                           1359,230 if n=='left' else 450]
    return v

class FlexibleCoreTest(unittest.TestCase):
    def test_ref_input_arbitrary(self):
        for d in (1.75,2.0,1.23456,3.00001):
            c=make_scale(view('left'),d,[record() for _ in range(10)],image_size_wh=[4608,2592])
            self.assertAlmostEqual(c['mm_per_px']*63,d)
            self.assertAlmostEqual(c['rod_width_mm'],d)
            self.assertFalse(c['accuracy_validated'])
            self.assertEqual(validate_scale(c,view_name='left',image_size_wh=[4608,2592]),d/63)
    def test_frame_gate(self):
        m={'fusion':{'value':60,'unit':'px','valid_columns':34,'total_columns':40,
                     'edge_fit_residual_median_px':.2},'confidence_heuristic':.99}
        with self.assertRaisesRegex(CalibrationError,'valid fusion columns'):
            frame_record({'center_y':100.},m)
        m['fusion']['valid_columns']=40;m['fusion']['edge_fit_residual_median_px']=1.5
        with self.assertRaisesRegex(CalibrationError,'residual'):
            frame_record({'center_y':100.},m)
        m['fusion']['edge_fit_residual_median_px']=.2;m['confidence_heuristic']=.3
        with self.assertRaisesRegex(CalibrationError,'quality'):
            frame_record({'center_y':100.},m)
        m['confidence_heuristic']=.95
        self.assertEqual(frame_record({'center_y':100.},m)['fused_width_px'],60)
    def test_ten_robust_frames(self):
        vals=[63.01,63.0,63.02,62.99,63.0,63.01,63.0,63.0,63.02,80]
        c=aggregate_records([record(v) for v in vals],1.81234)
        self.assertEqual(c['valid_frames'],9)
        self.assertEqual(c['rejected_frames'],1)
        self.assertAlmostEqual(c['fused_pixel_width_px'],63.01,delta=.015)
        self.assertLess(c['frame_std_mm'],.001)
        self.assertFalse('accuracy_validated' in c)
    def test_insufficient_and_unstable_fail(self):
        with self.assertRaisesRegex(CalibrationError,'Need ≥8'):
            aggregate_records([record()]*7,1.75)
        with self.assertRaises(CalibrationError):
            aggregate_records([record(px) for px in [60,61,62,63,64,65,66,67,68,69]],1.75)
        with self.assertRaises(CalibrationError):
            aggregate_records([record(63,center=100+6*i) for i in range(10)],1.75)
    def test_mismatch_and_pixel_only_fusion(self):
        cal=make_scale(view('right'),1.77,[record()]*10,image_size_wh=[4608,2592])
        with self.assertRaises(CalibrationError):validate_scale(cal,view_name='left')
        with self.assertRaises(CalibrationError):validate_scale(cal,view_name='right',image_size_wh=[800,600])
        with self.assertRaises(MirrorFusionError):
            fuse_mirror_views({'left':{'fusion':{'unit':'px','value':63,'valid_columns':40}},
                               'right':{'fusion':{'unit':'px','value':65,'valid_columns':40}}})
    def test_restore_structure_rejects_crossed_mirrors(self):
        obj={'schema':'filament_dual_mirror_v1466','working_rotation_degrees_clockwise':90,
             'original_image_size_wh':[4608,2592],'reference_diameter_mm':1.75001,
             'views':{'left':{'search_roi_xyxy':[1320,40,1359,200]},
                      'right':{'search_roi_xyxy':[1320,350,1359,600]}}}
        self.assertEqual(len(validate_saved_state(obj)),2)
        obj['views']['right']['search_roi_xyxy'][0]=1322
        with self.assertRaisesRegex(ValueError,'ROI invalid'):
            validate_saved_state(obj)
        obj['views']['right']['search_roi_xyxy']=[1322,350,1361,600]
        with self.assertRaisesRegex(ValueError,'share'):
            validate_saved_state(obj)

class GUIV1466Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch('filament_inspector.ui.local_calibration.AUTOSAVE',Path(self.tmp.name)/'state.json')
        self.patch.start()
        self.app=LocalCalibrationUI()
        self.app.update_idletasks()
    def tearDown(self):
        self.app._close_app()
        self.patch.stop()
        self.tmp.cleanup()
    def prepare(self):
        app=self.app
        app.image_cv_raw=np.zeros((2592,4608),np.uint8)
        app.image_cv=np.zeros((10,10,3),np.uint8)
        app.background_cv=np.zeros((10,10,3),np.uint8)
        for n in ('left','right'):
            app.views[n]['search_roi_xyxy']=view(n)['search_roi_xyxy']
            app.views[n]['seed_gray_model']={'samples':[],'median':40,'mad':1}
        return app
    def test_ui_only_mirrors_and_no_real_direct(self):
        app=self.app
        self.assertEqual(tuple(app.mirror_choose['values']),('Left Mirror','Right Mirror'))
        self.assertFalse(app.v142_rod_button.winfo_ismapped())
        self.assertIsNone(app.views['direct']['profile'])
        self.assertIsNone(app.views['direct']['search_roi_xyxy'])
        self.assertEqual(tuple(app.result_table['displaycolumns']),('view','median','valid','confidence'))
    def test_save_restore_per_view_with_seed_and_camera(self):
        app=self.prepare()
        app.cal_reference.set('2.00123')
        for n in ('left','right'):
            app.views[n]['local_scale_calibration']=make_scale(app.views[n],2.00123,
                [record(center=140 if n=='left' else 360)]*10,image_size_wh=[4608,2592],
                camera_settings={'lens_position':'9.750'})
        p=Path(self.tmp.name)/'saved.json'
        self.assertTrue(app._save_setup(p))
        saved=json.loads(p.read_text())
        self.assertNotIn('direct',saved['views'])
        self.assertEqual(saved['reference_diameter_mm'],2.00123)
        app.views['left']['seed_gray_model']=None
        app.views['right']['local_scale_calibration']=None
        app._apply_setup_obj(saved)
        self.assertIsNotNone(app.views['left']['seed_gray_model'])
        self.assertIsNotNone(app.views['right']['local_scale_calibration'])
        self.assertIsNone(app.views['direct']['search_roi_xyxy'])
    def test_both_calibration_failure_leaves_existing_scales(self):
        app=self.prepare()
        old={'mm_per_px':.027,'view_name':'Left Mirror','schema':'mirror_local_scale_v142',
             'search_roi_xyxy_at_calibration':view('left')['search_roi_xyxy'],
             'search_roi_center_xy':[1339.5,140.0],
             'working_rotation_degrees_clockwise':90,'original_image_size_wh':[4608,2592]}
        app.views['left']['local_scale_calibration']=old.copy()
        app.views['right']['local_scale_calibration']=None
        app._calibration_job={'views':('left','right'),'reference':1.75,
            'samples':{'left':[record(center=140)]*10,'right':[record(center=360)]*3},
            'camera':{'lens_position':'9.750'}, 'attempted':10,'awaiting':False}
        with patch('filament_inspector.ui.local_calibration.messagebox.showerror'):
            app._finish_calibration()
        self.assertEqual(app.views['left']['local_scale_calibration'],old)
        self.assertIsNone(app.views['right']['local_scale_calibration'])
        self.assertIsNone(app._calibration_job)
    def test_ten_frame_success_preserves_two_scales_and_fusion(self):
        app=self.prepare()
        app._calibration_job={'views':('left','right'),'reference':1.9345,
            'samples':{'left':[record(65.0,center=140)]*10,
                       'right':[record(67.0,center=360)]*10},
            'camera':{'lens_position':'9.750'}, 'attempted':10,'awaiting':False}
        with patch('filament_inspector.ui.local_calibration.messagebox.showinfo'):
            app._finish_calibration()
        self.assertAlmostEqual(app.views['left']['local_scale_calibration']['mm_per_px'],1.9345/65)
        self.assertAlmostEqual(app.views['right']['local_scale_calibration']['mm_per_px'],1.9345/67)
        self.assertTrue((Path(self.tmp.name)/'state.json').exists())
        self.assertEqual(app.views['left']['local_scale_calibration']['calibration_quality_metrics']['valid_frames'],10)
    def test_live_capture_measure_bypasses_direct_roi(self):
        app=self.prepare()
        lc=app.live_controls
        lc.background_epoch=('sess','1','1')
        with patch.object(app,'_previous_capture_handler') as handler, \
             patch.object(app,'_v14_process_all') as processed:
            app._handle_mirror_capture('measure',b'placeholder',{})
            handler.assert_called_once_with('current',b'placeholder',{})
            processed.assert_called_once()
    def test_new_rois_without_direct_and_pixel_only(self):
        app=self.prepare()
        app._check_mirror_prereqs() # no Direct ROI exists
        self.assertIsNone(app.views['direct']['search_roi_xyxy'])

    def test_one_side_calibration_does_not_need_other_seed(self):
        app=self.prepare()
        app.views['right']['seed_gray_model']=None
        app.views['right']['search_roi_xyxy']=None
        app._check_mirror_prereqs(('left',))
        with self.assertRaises(ValueError):app._check_mirror_prereqs(('left','right'))

    def test_closed_before_first_image_keeps_pending_saved_state(self):
        obj={'schema':'filament_dual_mirror_v1466','working_rotation_degrees_clockwise':90,
             'original_image_size_wh':[4608,2592],'reference_diameter_mm':1.75,
             'views':{'left':{},'right':{}}}
        p=Path(self.tmp.name)/'state.json'
        p.write_text(json.dumps(obj),encoding='utf-8')
        self.app.image_cv_raw=None
        self.app._startup_state=obj
        self.app._autosave_once()
        self.assertEqual(json.loads(p.read_text()),obj)

    def test_ten_camera_capture_requests_without_pi(self):
        app=self.prepare()
        app.live_controls.current_epoch=('sess','focus','exp')
        app.live_controls.background_epoch=app.live_controls.current_epoch
        app._calibration_job={'views':('left',),'reference':1.75555,
             'samples':{'left':[]},'camera':{'focus_epoch':None},
             'expected_epoch':app.live_controls.current_epoch,
             'roi':{'left':list(app.views['left']['search_roi_xyxy'])},
             'attempted':0,'awaiting':False,'reasons':[]}
        fake_measure=({'center_y':140.},{'fusion':{'value':61.0,'unit':'px',
           'valid_columns':40,'total_columns':40,'edge_fit_residual_median_px':.15},
           'confidence_heuristic':.95},None)
        app.live_controls.native_size_wh=(4608,2592)
        with patch.object(app.live_controls,'_capture') as capture, \
             patch('filament_inspector.ui.local_calibration.measure_dynamic',return_value=fake_measure), \
             patch('filament_inspector.ui.local_calibration.messagebox.showinfo'):
            for i in range(FRAME_COUNT):
                app._next_calibration_frame()
                self.assertTrue(app._calibration_job['awaiting'])
                self.assertEqual(app._calibration_job['attempted'],i+1)
                app._accept_calibration_snapshot({})
            self.assertEqual(capture.call_count,10)
            self.assertEqual(len(app._calibration_job['samples']['left']),10)
            app._finish_calibration()
        self.assertAlmostEqual(app.views['left']['local_scale_calibration']['mm_per_px'],1.75555/61.0)

if __name__=='__main__':unittest.main(verbosity=2)

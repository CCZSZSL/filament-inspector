"""v14.6.7 position scale / GUI / continuous offline regressions."""
from __future__ import annotations
from copy import deepcopy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from filament_inspector.ui.position import PositionCompensationUI
from filament_inspector.ui.local_calibration import LocalCalibrationUI
from filament_inspector.calibration.local import CalibrationError, make_scale
from filament_inspector.calibration.position import (
    initial_profile, validate_profile, profile_ready, with_position_point, scale_at_position)
from filament_inspector.algorithms.views import new_view
from filament_inspector.algorithms.fusion import fuse_mirror_views, MirrorFusionError


def records(p,y):
    return [dict(fused_width_px=float(p),tracked_center_y_px=float(y),valid_columns=40,
                 quality_score=.98,edge_fit_residual_px=.10) for _ in range(10)]


def build_view(n='left', center=100.):
    v=new_view(n)
    v['search_roi_xyxy']=[700,40,739,260]
    cal=make_scale(v,1.75,records(63,center),image_size_wh=[4608,2592],
                   camera_settings={'lens_position':'9.750'})
    v['local_scale_calibration']=cal
    return v


def ready_profile(cal,mid=100.):
    prof=with_position_point(None,cal,records(62.1,mid-20))
    return with_position_point(prof,cal,records(63.9,mid+20))


class PositionCoreTest(unittest.TestCase):
    def setUp(self):
        self.view=build_view()
        self.cal=self.view['local_scale_calibration']

    def test_require_both_sides_and_no_extrapolation(self):
        prof=with_position_point(None,self.cal,records(62.1,80))
        self.assertFalse(profile_ready(prof,self.cal))
        with self.assertRaises(CalibrationError):scale_at_position(prof,self.cal,90)
        prof=with_position_point(prof,self.cal,records(63.9,120))
        self.assertTrue(profile_ready(prof,self.cal))
        self.assertEqual(len(validate_profile(prof,self.cal)),3)
        self.assertIsNone(scale_at_position(prof,self.cal,75))
        self.assertIsNone(scale_at_position(prof,self.cal,126))
        self.assertAlmostEqual(scale_at_position(prof,self.cal,100),self.cal['mm_per_px'])
        self.assertAlmostEqual(scale_at_position(prof,self.cal,80),1.75/62.1)
        self.assertAlmostEqual(scale_at_position(prof,self.cal,120),1.75/63.9)

    def test_position_compensation_corrects_intermediate_width(self):
        prof=ready_profile(self.cal)
        # Simulate a diameter-constant moving rod; its projected pixel width
        # follows a deterministic position/magnification trend.
        for y in (80,90,100,110,120):
            expected_reference_px=63+(y-100)*0.045
            # In-between scale curve is close to reciprocal pixel width.
            measured=expected_reference_px*scale_at_position(prof,self.cal,y)
            self.assertLess(abs(measured-1.75),0.00020)
        self.assertGreater(abs(63.9*self.cal['mm_per_px']-1.75),.02)

    def test_failures_keep_original_profile_untouched(self):
        original=ready_profile(self.cal)
        copy=deepcopy(original)
        for r in (records(63,100),records(70,135),records(64,135)[:3]):
            with self.assertRaises(CalibrationError):with_position_point(original,self.cal,r)
        self.assertEqual(original,copy)

    def test_invalid_and_stale_profile_rejected(self):
        prof=ready_profile(self.cal)
        changed=deepcopy(self.cal)
        changed['calibration_timestamp']='2099-01-01'
        with self.assertRaises(CalibrationError):validate_profile(prof,changed)
        prof['points'][1]['scale_mm_per_px']*=1.12
        with self.assertRaises(CalibrationError):validate_profile(prof,self.cal)

    def test_two_independent_mm_before_virtual_fusion(self):
        a=build_view('left',100)['local_scale_calibration']
        b=build_view('right',400)['local_scale_calibration']
        pa=ready_profile(a,100);pb=ready_profile(b,400)
        a_y=95;b_y=405
        left_px=63+(a_y-100)*0.045
        right_px=63+(b_y-400)*0.045
        values={}
        for key,p,prof,cal,y in [('left',left_px,pa,a,a_y),('right',right_px,pb,b,b_y)]:
            values[key]={'fusion':{'unit':'mm','value':p*scale_at_position(prof,cal,y),
                                   'valid_columns':40},'physical_stats':{'median':1.75},
                         'scale_mm_per_px':scale_at_position(prof,cal,y)}
        f=fuse_mirror_views(values)
        self.assertAlmostEqual(f['value'],(f['left_mm']+f['right_mm'])/2)
        self.assertLess(abs(f['value']-1.75),.0002)
        self.assertEqual(f['unit'],'mm')
        values['right']['fusion']['unit']='px'
        with self.assertRaises(MirrorFusionError):fuse_mirror_views(values)


class GuiTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=PositionCompensationUI()
        self.app.withdraw()
        self.app.image_cv_raw=np.zeros((2592,4608),dtype=np.uint8)
        for n,center in [('left',100),('right',400)]:
            v=build_view(n,center)
            v['search_roi_xyxy']=[700,0 if n=='left' else 290,739,
                                   210 if n=='left' else 510]
            # base X only must match
            v['seed_gray_model']={'samples':[],'median':33,'mad':1}
            self.app.views[n]=v
        self.app.cal_reference.set('1.75')

    def tearDown(self):
        self.app._startup_state=None
        try:self.app.live_controls.close()
        except Exception:pass
        self.app.destroy()
        self.temp.cleanup()

    def add_curves(self):
        for n in ('left','right'):
            cal=self.app.views[n]['local_scale_calibration']
            yc=cal['search_roi_center_xy'][1]
            self.app.views[n]['position_scale_profile']=ready_profile(cal,yc)

    def test_gui_has_button_and_no_duplicate_checkbox(self):
        self.assertIn('START CONTINUOUS',self.app.continuous_button.cget('text'))
        self.assertTrue(self.app.position_enabled.get())
        self.assertIsNone(self.app.views['direct']['search_roi_xyxy'])
        def find(root):
            return [w for w in root.winfo_children() if isinstance(w, __import__('tkinter').ttk.Checkbutton)]
        # Actual layout is a parent frame of hidden legacy checkbox.
        controls=self.app.live_controls
        def walk(w):
            for child in w.winfo_children():
                if getattr(child,'cget',None) and isinstance(child,__import__('tkinter').ttk.Checkbutton):
                    if 'Continuous measure' in str(child.cget('text')):
                        return child.master.winfo_manager()
                sub=walk(child)
                if sub is not None:return sub
        self.assertEqual(walk(controls),'')

    def test_save_load_position_profile_atomic(self):
        self.add_curves()
        path=Path(self.temp.name)/'settings.json'
        self.assertTrue(self.app._save_setup(path))
        obj=json.loads(path.read_text())
        self.assertEqual(obj['app_version'],'v14.6.7')
        self.assertEqual(len(obj['views']['left']['position_scale_profile']['points']),3)
        self.app.views['left']['position_scale_profile']=None
        self.app._apply_setup_obj(obj)
        self.assertTrue(profile_ready(self.app.views['left']['position_scale_profile'],
                                      self.app.views['left']['local_scale_calibration']))
        bad=deepcopy(obj)
        bad['views']['right']['position_scale_profile']['base_calibration_timestamp']='bad'
        old=deepcopy(self.app.views['left']['position_scale_profile'])
        with self.assertRaises(CalibrationError):self.app._apply_setup_obj(bad)
        self.assertEqual(old,self.app.views['left']['position_scale_profile'])

    def test_offset_capture_rollback(self):
        self.add_curves()
        old={n:deepcopy(self.app.views[n]['position_scale_profile']) for n in ('left','right')}
        self.app._calibration_job={'purpose':'y_position','views':('left','right'),
            'samples':{'left':records(62,150),'right':records(64,400)},'reference':1.75}
        with patch('filament_inspector.ui.local_calibration.messagebox.showerror'):
            self.app._finish_calibration()
        self.assertIsNone(self.app._calibration_job)
        for n in old:self.assertEqual(old[n],self.app.views[n]['position_scale_profile'])

    def test_position_success_commit_two_views(self):
        self.app._calibration_job={'purpose':'y_position','views':('left','right'),
            'samples':{'left':records(62.1,80),'right':records(62.1,380)},'reference':1.75}
        path=Path(self.temp.name)/'setup.json'
        with patch.object(self.app,'_save_setup',return_value=True), \
             patch('filament_inspector.ui.position.messagebox.showinfo'):
            self.app._finish_calibration()
        for n in ('left','right'):
            self.assertEqual(len(self.app.views[n]['position_scale_profile']['points']),2)

    def test_continuous_start_stop_and_log(self):
        from filament_inspector.ui.position import ROOT
        lc=self.app.live_controls
        lc.background_epoch=('s','f','e')
        self.app.background_cv=object()
        with patch('filament_inspector.ui.position.ROOT',Path(self.temp.name)), \
             patch.object(self.app,'_check_mirror_prereqs'),patch.object(lc,'_schedule_auto'):
            self.app._toggle_continuous_button()
            self.assertTrue(lc.continuous.get())
            self.assertIn('STOP CONTINUOUS',self.app.continuous_button.cget('text'))
            self.assertIsNotNone(self.app._continuous_log)
            self.app.f_direct_result={'value':1.75,'left_mm':1.74,'right_mm':1.76,'absolute_difference_mm':.02}
            self.app.view_results={'left':{'fusion':{'valid_columns':40},'tracking_center_y_px':100},
                                   'right':{'fusion':{'valid_columns':39},'tracking_center_y_px':400}}
            path=self.app._continuous_log
            self.app._record_continuous_result()
            self.app._toggle_continuous_button()
            self.assertFalse(lc.continuous.get())
            with open(path,newline='') as f: rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['f_direct_mm'],'1.75')
            self.assertEqual(rows[0]['right_valid_columns'],'39')

    def test_position_mm_conversion_from_pixel_only_once(self):
        self.add_curves()
        app=self.app
        def fake_parent_process(this,n):
            cal=this.views[n]['local_scale_calibration']
            self.assertIsNone(cal)
            base=this.views[n]['position_scale_profile']['base_center_y_px']
            y=base+10
            p=63+10*.045
            this.view_results[n]={'fusion':{'value':p,'unit':'px','valid_columns':40,
                                            'total_columns':40,'column_std':.1,
                                            'column_min':p-.1,'column_max':p+.1,
                                            'edge_fit_residual_median_px':.1},
                         'tracking_center_y_px':y,'calibration_status':'pixel only',
                         'confidence_heuristic':.99,'roi':[700,0,739,200]}
            this.engines[n]=SimpleNamespace(valid=np.ones(40,bool),widths_px=np.full(40,p),
                 diameters_mm=None,reference_correction_factor=None)
        with patch.object(LocalCalibrationUI,'_process_one',autospec=True,side_effect=fake_parent_process):
            for n in ('left','right'):app._process_one(n)
        for n in ('left','right'):
            m=app.view_results[n]
            self.assertEqual(m['fusion']['unit'],'mm')
            self.assertAlmostEqual(m['fusion']['value'],1.75,places=4)
            self.assertEqual(m['position_compensation']['mode'],'Y-interpolated')
        f=fuse_mirror_views(app.view_results)
        self.assertAlmostEqual(f['value'],1.75,places=4)
        self.assertIsNotNone(app.views['left']['local_scale_calibration'])

if __name__=='__main__':unittest.main()

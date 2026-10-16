"""v14.6.8 position experiments. GUI tests require xvfb-run on Linux."""
from __future__ import annotations
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from filament_inspector.recording.experiments import (
    ExperimentLog, analyse, extract_result, new_row, paired_correlation, outlier_flags)
from filament_inspector.ui.experiments import ExperimentUI


def measured(y=100., px=63., scale=1.75/63, quality=.94):
    return {'fusion':{'value':px*scale,'unit':'mm','valid_columns':39,
                      'edge_fit_residual_median_px':0.12},
            'tracking_center_y_px':y, 'confidence_heuristic':quality,
            'scale_mm_per_px':scale, 'calibration_status':'MIRROR BASE SCALE'}


class AnalysisTest(unittest.TestCase):
    def test_raw_pixels_preserved_when_mm_and_pixel_only(self):
        row=new_row(1,'motion')
        extract_result(row,{'left':measured(100,63),
                            'right':{'fusion':{'value':64.,'unit':'px','valid_columns':40},
                                     'tracking_center_y_px':410.,'confidence_heuristic':.72,
                                     'calibration_status':'Pixel Only: outside Y curve'}},
                       None,{'right':'last failure should not override valid pixels'})
        self.assertAlmostEqual(row['left_pixel_width'],63)
        self.assertAlmostEqual(row['left_diameter_mm'],1.75)
        self.assertAlmostEqual(row['right_pixel_width'],64)
        self.assertEqual(row['right_diameter_mm'],'')
        self.assertEqual(row['frame_status'],'PIXEL_ONLY')
        self.assertIn('outside Y',row['failure_reason'])

    def test_stats_motion_position_slope_and_zero_variance(self):
        rows=[]
        for i in range(100):
            row=new_row(i+1,'motion')
            row.update(left_y_px=i,right_y_px=500-i,
                       left_pixel_width=60+i*.04,right_pixel_width=64-i*.02,
                       left_diameter_mm=1.70+i*.001,right_diameter_mm=1.8-i*.001,
                       f_direct_mm=1.75,delta_lr_mm=abs(-.1+2*i*.001),
                       capture_status='OK',frame_status='VALID_MM')
            rows.append(row)
        report=analyse(rows,'motion',100)
        self.assertEqual(report['attempted_frames'],100)
        self.assertEqual(report['valid_mm_frames'],100)
        self.assertAlmostEqual(report['correlations']['left_y_px__vs__left_pixel_width']['pearson_r'],1)
        self.assertAlmostEqual(report['correlations']['right_y_px__vs__right_pixel_width']['slope'],.02)
        self.assertIsNone(report['correlations']['left_y_px__vs__f_direct_mm']['pearson_r'])
        self.assertEqual(report['stats']['f_direct_mm']['range'],0)
        self.assertFalse(report['stopped_early'])
        self.assertAlmostEqual(paired_correlation(rows,'left_y_px','left_pixel_width')['slope'],.04)

    def test_outliers_flagged_without_removal(self):
        rows=[]
        for i in range(50):
            row=new_row(i+1,'static')
            row.update(left_pixel_width=63.0+(0.001 if i%2 else 0.),
                       right_pixel_width=63.1,f_direct_mm=1.75)
            if i==28:row['left_pixel_width']=77.
            rows.append(row)
        self.assertIn('left_pixel_width',outlier_flags(rows)[29])
        report=analyse(rows,'static',50)
        self.assertEqual(report['stats']['left_pixel_width']['count'],50)
        self.assertIn(29,[o['attempt'] for o in report['outliers']])
        self.assertEqual(len(rows),50)

    def test_io_failures_and_partial_stop_keep_every_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            log=ExperimentLog(Path(d)/'static','static',50,{'mock':True})
            for i in range(11):
                row=new_row(i+1,'static')
                if i%3==0:row['failure_reason']='HTTP 500'
                else:
                    row.update(capture_status='OK',frame_status='VALID_MM',
                               left_pixel_width=63,right_pixel_width=64,
                               left_diameter_mm=1.75,right_diameter_mm=1.74,f_direct_mm=1.745)
                log.append(row)
            report=log.finish(stopped=True)
            with (log.csv_path).open(newline='') as fp:records=list(csv.DictReader(fp))
            self.assertEqual(len(records),11)
            self.assertEqual(report['valid_mm_frames'],7)
            self.assertEqual(len(report['invalid_frames']),4)
            self.assertTrue(report['stopped_early'])
            self.assertEqual(json.loads(log.json_path.read_text())['attempted_frames'],11)
            self.assertTrue(log.text_path.exists())
            self.assertTrue((log.output_dir/'outlier_flags.csv').exists())


class GUITest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.output_patch=patch('filament_inspector.ui.experiments.OUTPUT_ROOT',Path(self.tmp.name))
        self.output_patch.start()
        self.app=ExperimentUI()
        self.app.withdraw()
        lc=self.app.live_controls
        lc.background_epoch=('session','focus','settings')
        self.app.background_cv=object()
        self.app.image_cv_raw=np.zeros((2592,4608),np.uint8)
        for n in ('left','right'):
            self.app.views[n]['search_roi_xyxy']=[400,30 if n=='left' else 300,
                                                  439,200 if n=='left' else 500]
            self.app.views[n]['seed_gray_model']={'samples':[],'median':30.,'mad':1.}
        self._original_commit=self.app._commit_fields
        self._orig_check=self.app._check_mirror_prereqs
        self.app._commit_fields=lambda:None
        self.app._check_mirror_prereqs=lambda *a,**k:None
        self.fake_mode='mm'
        self.app._previous_capture_handler=self._fake_capture
        self.app._v14_process_all=self._fake_measure
        # Worker returns from the same request method but never hits actual Pi.
        lc._request=lambda url,endpoint,timeout=60:(b'example PNG bytes',
                 {'X-Session-Id':'session','X-Focus-Epoch':'focus','X-Settings-Epoch':'settings'})

    def _fake_capture(self,kind,blob,headers):
        if blob==b'bad':raise ValueError('PNG is incomplete')
        self.assertEqual(kind,'current')
        self.assertEqual(self.app.live_controls.native_size_wh,(4608,2592))

    def _fake_measure(self):
        job=self.app._exp_job
        i=job['attempts']
        self.app.view_results={'left':measured(100+i*.05,63+i*.001),
                               'right':measured(405-i*.05,64-i*.001)}
        self.app.f_direct_result={'value':1.75,'absolute_difference_mm':.02}
        if self.fake_mode=='pixel':
            self.app.view_results['right']['fusion']={'value':64.,'unit':'px','valid_columns':37}
            self.app.view_results['right']['calibration_status']='Pixel Only: Y outside range'
            self.app.f_direct_result=None
        if self.fake_mode=='fail':raise RuntimeError('Injected analysis failure')

    def tearDown(self):
        try:
            if self.app._exp_job:
                if self.app._exp_job['inflight']:
                    self.app._exp_job['inflight']=False
                self.app._exp_finish(stopped=True)
            self.app.live_controls.close()
            self.app.destroy()
        finally:
            self.output_patch.stop();self.tmp.cleanup()

    def _drain(self):
        from time import monotonic,sleep
        deadline=monotonic()+2
        while self.app._exp_queue.empty():
            if monotonic()>deadline:self.fail('worker never returned')
            sleep(.002)
        if self.app._exp_poll_id is not None:
            self.app.after_cancel(self.app._exp_poll_id)
            self.app._exp_poll_id=None
        self.app._exp_poll()
        if self.app._exp_after is not None:
            self.app.after_cancel(self.app._exp_after)
            self.app._exp_after=None

    def test_buttons_and_busy_conflict(self):
        self.assertEqual(self.app.exp_static_button.cget('text'),'START STATIC TEST — 50 FRAMES')
        self.assertEqual(self.app.exp_motion_button.cget('text'),'START MOTION TEST — 100 FRAMES')
        self.app._exp_start('static',50)
        self.assertIsNotNone(self.app._exp_job)
        self.assertIn('disabled',self.app.exp_static_button.state())
        with patch('filament_inspector.ui.experiments.messagebox.showwarning') as warning:
            self.app._exp_start('motion',100)
            self.app._toggle_continuous_button()
            self.assertEqual(warning.call_count,2)
        self.assertFalse(self.app.live_controls.continuous.get())
        self._drain()
        self.app._exp_stop()
        self.assertIsNone(self.app._exp_job)
        self.assertNotIn('disabled',self.app.exp_static_button.state())

    def test_full_50_static_and_100_motion_attempts(self):
        for name,target in [('static',50),('motion',100)]:
            self.app._exp_start(name,target)
            self.assertIsNotNone(self.app._exp_job)
            log=self.app._exp_job['log']
            for i in range(target):
                if i>0:self.app._exp_next()
                self._drain()
            self.assertIsNone(self.app._exp_job)
            with log.csv_path.open(newline='') as fp:rows=list(csv.DictReader(fp))
            self.assertEqual(len(rows),target)
            self.assertAlmostEqual(float(rows[0]['left_pixel_width']),63.001,places=9)
            self.assertEqual(json.loads(log.json_path.read_text())['valid_mm_frames'],target)
            self.assertIn('COMPLETE',self.app.exp_status.get())

    def test_failed_transport_and_pixel_only_logged(self):
        self.app._exp_start('motion',100)
        log=self.app._exp_job['log']
        self._drain()
        self.app.live_controls._request=lambda *a,**k:(_ for _ in ()).throw(OSError('network broken'))
        self.app._exp_next();self._drain()
        self.fake_mode='pixel'
        self.app.live_controls._request=lambda *a,**k:(b'example PNG bytes',
                    {'X-Session-Id':'session','X-Focus-Epoch':'focus','X-Settings-Epoch':'settings'})
        self.app._exp_next();self._drain()
        self.app._exp_stop()
        with log.csv_path.open(newline='') as fp:rows=list(csv.DictReader(fp))
        self.assertEqual(len(rows),3)
        self.assertIn('network broken',rows[1]['failure_reason'])
        self.assertEqual(rows[2]['frame_status'],'PIXEL_ONLY')
        self.assertEqual(rows[2]['right_pixel_width'],'64.0')
        self.assertEqual(rows[2]['f_direct_mm'],'')
        self.assertEqual(json.loads(log.json_path.read_text())['successful_snapshots'],2)

    def test_real_gray8_png_snapshot_validation_path(self):
        """No Pi needed: exercise original client PNG decode/epoch/metadata path."""
        import cv2
        from filament_inspector.ui.camera_controls import IntegratedLiveControls
        lc=self.app.live_controls
        lc.capture_dir=Path(self.tmp.name)/'captures'
        self.app.background_cv=np.zeros((4608,2592,3),dtype=np.uint8)
        native=np.full((2592,4608),127,dtype=np.uint8)
        encoded=cv2.imencode('.png',native)[1].tobytes()
        self.assertTrue(encoded.startswith(b'\x89PNG\r\n\x1a\n'))
        headers={'X-Session-Id':'session','X-Focus-Epoch':'focus',
                 'X-Settings-Epoch':'settings','X-Exposure-Time-Us':'1000'}
        lc._request=lambda *a,**k:(encoded,headers)
        # Reuse precisely the same callback as v14.6.7, with actual PNG checks.
        self.app._previous_capture_handler=lambda kind,blob,h:IntegratedLiveControls._handle_capture(lc,kind,blob,h)
        self.app._exp_start('static',50)
        log=self.app._exp_job['log']
        self._drain()
        self.assertEqual(log.rows[0]['capture_status'],'OK')
        self.assertEqual(log.rows[0]['frame_status'],'VALID_MM')
        self.assertEqual(log.rows[0]['snapshot_bytes'],len(encoded))
        self.assertEqual((lc.capture_dir/'current_latest.png').read_bytes(),encoded)
        self.assertEqual(json.loads((lc.capture_dir/'current_latest.json').read_text())['pixel_format'],
                         'GRAY8_PNG_LOSSLESS')
        self.app._exp_stop()

    def test_stop_during_pending_capture_writes_current_frame(self):
        self.app._exp_start('static',50)
        log=self.app._exp_job['log']
        self.app._exp_stop()
        self.assertIsNotNone(self.app._exp_job)
        self._drain()
        self.assertIsNone(self.app._exp_job)
        with log.csv_path.open(newline='') as fp:rows=list(csv.DictReader(fp))
        self.assertEqual(len(rows),1)
        self.assertTrue(json.loads(log.json_path.read_text())['stopped_early'])

    def test_bad_png_not_silently_removed_and_next_frames_run(self):
        self.app.live_controls._request=lambda *a,**k:(b'bad',{})
        self.app._exp_start('static',50)
        log=self.app._exp_job['log']
        self._drain()
        self.assertEqual(log.rows[0]['frame_status'],'INVALID')
        self.assertIn('incomplete',log.rows[0]['failure_reason'])
        self.app._exp_stop()
        self.assertEqual(json.loads(log.json_path.read_text())['attempted_frames'],1)


if __name__=='__main__':unittest.main()

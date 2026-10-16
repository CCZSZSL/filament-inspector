"""Regression tests for v14.7.1 Fast ROI features through the current v14.7.2 UI."""
import io
import unittest
from unittest import mock
import numpy as np
import cv2
from filament_inspector.acquisition.roi import choose_band, sensor_row_range, install_band_in_place, decode_validate_roi_png


class RoiTests(unittest.TestCase):
    def test_center_band_and_edges(self):
        self.assertEqual(choose_band([1200,100,1239,160],[1200,300,1239,360]),(1188,64))
        self.assertEqual(choose_band([0,100,39,160],[0,300,39,360]),(0,64))
        self.assertEqual(choose_band([2552,100,2591,160],[2552,300,2591,360]),(2528,64))
        for a,b in [(0,64),(1234,64),(2528,64)]:
            y0,y1=sensor_row_range(a,b)
            self.assertEqual((y0,y1),(2592-a-b,2592-a))
        with self.assertRaises(ValueError):choose_band([1200,100,1239,200],[1201,300,1240,380])

    def test_same_pixels_without_interpolation(self):
        # Representative independent BGR caches. Native sensor sizes are
        # tested separately as the helper accepts any valid sensor dimensions.
        h,w=320,500
        rng=np.random.default_rng(34)
        raw=rng.integers(0,256,(h,w),dtype=np.uint8)
        old_gray=np.full_like(raw,42)
        old_sensor=cv2.cvtColor(old_gray,cv2.COLOR_GRAY2BGR)
        old_work=cv2.rotate(old_sensor,cv2.ROTATE_90_CLOCKWISE)
        x0,width=137,64
        y0,y1=sensor_row_range(x0,width,h)
        band=raw[y0:y1,:].copy()
        install_band_in_place(old_work,old_sensor,band,x0)
        full_rot=cv2.rotate(cv2.cvtColor(raw,cv2.COLOR_GRAY2BGR),cv2.ROTATE_90_CLOCKWISE)
        self.assertTrue(np.array_equal(full_rot[:,x0:x0+width,:],old_work[:,x0:x0+width,:]))
        self.assertTrue(np.array_equal(old_sensor[y0:y1,:,:],cv2.cvtColor(band,cv2.COLOR_GRAY2BGR)))
        self.assertTrue(np.all(old_sensor[:y0]==42))
        self.assertTrue(np.all(old_work[:,:x0]==42))

    def test_real_native_size_roi_roundtrip(self):
        h,w=2592,4608;x0=1104;width=64
        # Native camera band 64x4608 remains GRAY8 PNG.
        a=np.tile(np.arange(w,dtype=np.uint16)%256,(width,1)).astype('uint8')
        ok,enc=cv2.imencode('.png',a,[cv2.IMWRITE_PNG_COMPRESSION,1]);self.assertTrue(ok)
        headers={'X-ROI-Working-X0':str(x0),'X-ROI-Width':str(width),
                 'X-Full-Image-Width':str(w),'X-Full-Image-Height':str(h),
                 'X-Pixel-Format':'GRAY8','X-Session-ID':'sess','X-Focus-Epoch':'1','X-Settings-Epoch':'3'}
        image,epoch=decode_validate_roi_png(enc.tobytes(),headers,x0,width,expected_epoch=('sess','1','3'))
        self.assertTrue(np.array_equal(a,image));self.assertEqual(epoch,('sess','1','3'))
        with self.assertRaisesRegex(ValueError,'focus/exposure/session'):
            decode_validate_roi_png(enc.tobytes(),headers,x0,width,expected_epoch=('sess','2','3'))
        with self.assertRaisesRegex(ValueError,'origin mismatch'):
            decode_validate_roi_png(enc.tobytes(),headers,x0+1,width)
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            decode_validate_roi_png(enc.tobytes()[:-20],headers,x0,width)

    def test_roi_source_matches_crop_then_gray(self):
        rng=np.random.default_rng(12)
        rgb=rng.integers(0,256,(180,220,3),dtype=np.uint8)
        x0,width=64,40;y0,y1=sensor_row_range(x0,width,180)
        from_full=cv2.cvtColor(rgb,cv2.COLOR_BGR2GRAY)[y0:y1,:]
        from_crop=cv2.cvtColor(rgb[y0:y1,:,:],cv2.COLOR_BGR2GRAY)
        self.assertTrue(np.array_equal(from_full,from_crop))

    def test_current_web_retains_heatmap_controls(self):
        from filament_inspector.web.server import WEB_DIR
        self.assertTrue((WEB_DIR/'dashboard.js').is_file())
        html=(WEB_DIR/'index.html').read_text(encoding='utf-8')
        self.assertIn('heatRejected',html)
        self.assertIn('heatMode',html)


class PiServiceTests(unittest.TestCase):
    def test_roi_endpoint_camera_capture_matches_full_pipeline(self):
        # Import Pi-only source using stubs; no Pi hardware or libcamera on CI.
        import importlib.util, sys, types, threading, time
        from pathlib import Path
        with unittest.mock.patch.dict(sys.modules,{'picamera2':types.ModuleType('picamera2'),
                                          'libcamera':types.ModuleType('libcamera')}):
            sys.modules['picamera2'].Picamera2=object
            sys.modules['libcamera'].controls=object
            spec=importlib.util.spec_from_file_location('pi_fast_stub_v1471',Path(__file__).resolve().parents[1]/'filament_inspector'/'camera.py')
            mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            s=mod.CameraService.__new__(mod.CameraService)
            class FakeCamera:
                def __init__(self,src):self.src=src
                def capture_array(self,stream):
                    if stream!='main':raise RuntimeError('Wrong stream')
                    return self.src
            # Smaller arbitrary sensor for runtime speed; sensor band mapping is exact.
            h,w=320,500
            rng=np.random.default_rng(18)
            rgb=rng.integers(0,256,(h,w,3),dtype=np.uint8)
            s.camera=FakeCamera(rgb)
            s.args=types.SimpleNamespace(main_size=(w,h))
            s.camera_lock=threading.Lock()
            s.focus_busy=False;s.focus_settle_until=0.
            s.session_id='stable'
            s.focus_epoch=3;s.settings_epoch=7
            s.exposure_us=1000;s.analogue_gain=2.0;s.lens_position=9.515
            s.fast_frame_counter=iter(range(1,100))
            blob,headers=s.snapshot_working_band(112,64)
            self.assertEqual(headers['X-Fast-Frame-ID'],'1')
            self.assertEqual(headers['X-ROI-Working-X0'],'112')
            self.assertEqual(headers['X-Full-Image-Height'],str(h))
            original=cv2.cvtColor(rgb,cv2.COLOR_BGR2GRAY)
            y0,y1=sensor_row_range(112,64,h)
            decoded=cv2.imdecode(np.frombuffer(blob,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
            self.assertTrue(np.array_equal(decoded,original[y0:y1]))
            with self.assertRaises(ValueError):s.snapshot_working_band(310,64)


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from filament_inspector.ui.app import FilamentInspectorApp
            cls.cls=FilamentInspectorApp
        except Exception as exc:
            raise unittest.SkipTest(str(exc))

    def setUp(self):
        try:self.app=self.cls()
        except Exception as exc:raise unittest.SkipTest('No Tk screen: '+str(exc))
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    def test_default_stable_path_and_toggle(self):
        self.assertFalse(self.app.fast_enabled.get())
        self.app.fast_enabled.set(True);self.app._fast_toggled()
        self.assertTrue(self.app.fast_enabled.get())
        self.app._fast_fallback()
        self.assertFalse(self.app.fast_enabled.get())

    def test_late_roi_dropped_without_original_full_frame_decode(self):
        from unittest.mock import Mock
        handler=Mock(side_effect=AssertionError('Original decoder was called'))
        self.app._fast_original_handle=handler
        self.app.fast_enabled.set(False)
        self.app.live_controls.continuous.set(False)
        self.app._fast_handle_capture('measure',b'incomplete',{'X-ROI-Working-X0':'1220'})
        handler.assert_not_called()

    def test_fast_cannot_run_without_calibration_and_stops(self):
        lc=self.app.live_controls
        lc.continuous.set(True)
        self.app.fast_enabled.set(True)
        self.app._fast_capture('measure')
        self.assertFalse(self.app.fast_enabled.get())
        self.assertFalse(lc.continuous.get())
        self.assertIn('FAST STOPPED',self.app.fast_status.get())

if __name__=='__main__':unittest.main()

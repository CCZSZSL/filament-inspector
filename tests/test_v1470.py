"""v14.7.0 LAN HTTP behavior and Mac Tk integration (offline tests)."""
import csv
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
import socket
import tempfile
import time
import unittest
from unittest import mock
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from filament_web_v1470 import DashboardStore, LanDashboardServer, csv_from_rows
from filament_profile_v1469 import ProfileRecorder

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.store=DashboardStore(max_rows=20)
        self.server=LanDashboardServer(self.store,host='127.0.0.1',port=0)
        self.server.start()
        self.base=f'http://127.0.0.1:{self.server.port}'
    def tearDown(self):self.server.stop()
    def get(self,path):
        with urlopen(self.base+path,timeout=3) as r:
            return r.status,r.headers,r.read()
    def test_public_index_offline_assets(self):
        for path,mime in [('/','text/html'),('/dashboard.js','application/javascript'),('/dashboard.css','text/css')]:
            status,headers,blob=self.get(path)
            self.assertEqual(status,200)
            self.assertIn(mime,headers['Content-Type'])
            self.assertGreater(len(blob),1000)
            self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertIn('nosniff',headers['X-Content-Type-Options'])
        html=self.get('/')[2].decode()
        for key in ['overview','diameter','mirrors','profile','experiments','edgeHeatmap','export.csv']:
            self.assertIn(key,html)
    def test_live_state_no_remote_controls(self):
        row={'timestamp':'2026-10-14T21:30+11:00','elapsed_s':0,'source':'continuous',
              'left_mm':1.74,'right_mm':1.76,'f_direct_mm':1.75,'delta_lr_mm':.02,
              'left_y_px':2080.5,'right_y_px':2444.5,'status':'VALID_MM'}
        self.store.publish(measurements=[row],mode='continuous',calibration='OK')
        x=json.loads(self.get('/api/state')[2])
        self.assertEqual(x['measurements'][0]['left_y_px'],2080.5)
        self.assertEqual(x['measurements'][0]['f_direct_mm'],1.75)
        self.assertEqual(x['mode'],'continuous')
        for bad in ['/api/start','/api/stop','/../filament_diameter_ui_v1469.py','/secret','/api/camera','/api/calibrate']:
            with self.assertRaises(HTTPError) as ex:urlopen(self.base+bad,timeout=3)
            self.assertEqual(ex.exception.code,404)
        with self.assertRaises(HTTPError) as ex:
            urlopen(Request(self.base+'/api/state',data=b'hi',method='POST'),timeout=3)
        self.assertEqual(ex.exception.code,405)
    def test_csv_exports_invalid_row_without_deletion(self):
        self.store.publish(measurements=[{'timestamp':'t1','f_direct_mm':1.75,'status':'VALID_MM','edge_columns_left':[61.2]*40},
             {'timestamp':'t2','f_direct_mm':None,'left_pixel_width':61.8,'status':'PIXEL_ONLY'},
             {'timestamp':'t3','f_direct_mm':None,'status':'CAPTURE_FAILED'}])
        status,headers,blob=self.get('/api/export.csv')
        self.assertEqual(status,200)
        self.assertIn('attachment',headers['Content-Disposition'])
        rows=list(csv.DictReader(StringIO(blob.decode('utf-8-sig'))))
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[1]['left_pixel_width'],'61.8')
        self.assertEqual(rows[0]['left_edge_col_00_px'],'61.2')
        self.assertEqual(rows[0]['left_edge_col_39_px'],'61.2')
        self.assertEqual(rows[2]['status'],'CAPTURE_FAILED')
    def test_snapshot_copy_clamp_and_atomic_publish(self):
        self.store.publish(measurements=[{'i':i} for i in range(100)])
        s=self.store.snapshot();self.assertEqual(len(s['measurements']),20)
        self.assertEqual(s['measurements'][0]['i'],80)
        with self.assertRaises(ValueError):self.store.publish(measurements=[{'bad':float('nan')}])
        self.assertEqual(self.store.snapshot()['measurements'][0]['i'],80)
        def writer(i):
            self.store.publish(measurements=[{'i':i,'x':1.751}],mode='continuous')
            return True
        def reader(_):
            snapshot=json.loads(self.get('/api/state')[2]);return snapshot['measurements'][0]['x']
        with ThreadPoolExecutor(max_workers=8) as pool:
            fs=[pool.submit(writer,i) if i%3==0 else pool.submit(reader,i) for i in range(75)]
            for f in fs:self.assertIsNotNone(f.result(timeout=7))
    def test_stop_and_restart_same_port(self):
        port=self.server.port
        self.server.stop()
        self.assertFalse(self.server.running)
        self.server=LanDashboardServer(self.store,host='127.0.0.1',port=port)
        self.server.start()
        self.assertEqual(self.get('/api/health')[0],200)
    def test_bad_port_and_suppressed_files(self):
        for port in [-1,80,65536,'8765']:
            with self.assertRaises(ValueError):LanDashboardServer(self.store,port=port)
        self.assertNotIn('access-control-allow-origin',dict((k.lower(),v) for k,v in self.get('/api/state')[1].items()))

class TkSmoke(unittest.TestCase):
    def setUp(self):
        try:
            from filament_diameter_ui_v1470 import FilamentUIV1470
            self.ui=FilamentUIV1470()
        except Exception as exc:
            if 'display' in str(exc).lower():self.skipTest('Headless Tk; run xvfb for full GUI')
            raise
    def tearDown(self):
        if hasattr(self,'ui'):
            try:self.ui._web_stop();self.ui.destroy()
            except Exception:pass
    def test_all_old_controls_and_web_server(self):
        u=self.ui
        for name in ['exp_static_button','exp_motion_button','continuous_button','profile_open_button',
                     'web_start_button','web_copy_button','web_stop_button','web_open_button']:
            self.assertTrue(getattr(u,name).winfo_exists())
        with mock.patch('filament_diameter_ui_v1470.network_addresses',return_value=['192.168.20.25']):
            # Avoid external port collisions; inspect URL before launch.
            u.web_port.set('0')  # invalid public setting; no side effect
            with mock.patch('tkinter.messagebox.showerror'):
                u._web_start()
            self.assertIsNone(u._web_server)
            with socket.socket() as s:
                s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            u.web_port.set(str(port));u._web_start()
            self.assertTrue(u._web_server.running)
            self.assertIn('http://192.168.20.25:',u.web_address.get())
            u._web_copy()
            self.assertIn('192.168.20.25',u.clipboard_get())
            u._exp_set_buttons(True)
            self.assertNotIn('disabled',u.web_open_button.state())
            u._exp_set_buttons(False)
            self.assertNotIn('disabled',u.web_open_button.state())
            self.assertIn('disabled',u.web_start_button.state())
            u._web_stop()
            self.assertIn('disabled',u.web_open_button.state())
            self.assertNotIn('disabled',u.web_start_button.state())
    def test_loaded_experiment_csv_keeps_y_and_pixel_fields(self):
        from unittest import mock
        from pathlib import Path
        u=self.ui
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'frames.csv'
            with path.open('w',newline='') as stream:
                w=csv.DictWriter(stream,fieldnames=['timestamp','test','frame_status',
                     'left_diameter_mm','right_diameter_mm','f_direct_mm','left_y_px',
                     'right_y_px','left_pixel_width','right_pixel_width','left_calibration_status'])
                w.writeheader()
                w.writerow({'timestamp':'2026-10-14T20:00:00+11:00','test':'motion',
                            'frame_status':'PIXEL_ONLY','left_y_px':2080.5,'right_y_px':2443.5,
                            'left_pixel_width':61.22,'right_pixel_width':61.65,
                            'left_calibration_status':'OUTSIDE valid range'})
            with mock.patch('tkinter.filedialog.askopenfilename',return_value=str(path)):
                u._profile_load()
            row=u._web_store.snapshot()['measurements'][0]
            self.assertIsNone(row['axial_mm'])
            self.assertEqual(row['left_y_px'],'2080.5')
            self.assertEqual(row['left_pixel_width'],'61.22')
            self.assertIsNone(row['f_direct_mm'])

    def test_measurement_published_after_inherited_computation(self):
        from filament_diameter_ui_v1469 import FilamentUIV1469
        u=self.ui
        class Engine:
            widths_px=[61.2]*40
            valid=[True]*40
        def fake_measure(self):
            self.view_results={}
            for n in ('left','right'):
                self.view_results[n]={
                  'fusion':{'value':1.75,'unit':'mm','valid_columns':40,
                            'accepted_indices':list(range(40))},
                  'scale_mm_per_px':.0285,'tracking_center_y_px':2090.,
                  'confidence_heuristic':1.,'calibration_status':'VALID SCALE'}
            self.engines={'left':Engine(),'right':Engine()}
            self.f_direct_result={'value':1.75,'left_mm':1.75,'right_mm':1.75,
                                 'absolute_difference_mm':0.}
            self.profile_recorder.append(at_s=time.monotonic(),timestamp='2026-10-14T22:00:00+11:00',
               source='manual',left_mm=1.75,right_mm=1.75,f_direct_mm=1.75,
               delta_lr_mm=0,status='VALID_MM')
        with mock.patch.object(FilamentUIV1469,'_v14_process_all',fake_measure):
            u._v14_process_all()
        row=u._web_store.snapshot()['measurements'][0]
        self.assertEqual(row['left_y_px'],2090)
        self.assertEqual(row['left_valid_columns'],40)
        self.assertEqual(row['left_pixel_width'],1.75/.0285)
        self.assertEqual(row['edge_columns_left'][0],61.2)
        self.assertEqual(len(row['edge_columns_right']),40)
        u._profile_clear()
        self.assertFalse(u._web_store.snapshot()['measurements'])

if __name__=='__main__':unittest.main()

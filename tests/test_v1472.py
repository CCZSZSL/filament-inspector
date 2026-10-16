"""v14.7.2 UI-only compatibility and local read-only service tests.

No camera, sensor, Pi, or online dependency is required.
"""
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import json
import unittest
from filament_inspector.web.server import DashboardStore, LanDashboardServer, WEB_DIR


class WebAestheticTests(unittest.TestCase):
    def test_all_pages_and_main_controls_retained(self):
        html=(WEB_DIR/'index.html').read_text(encoding='utf-8')
        for name in ('overview','diameter','mirrors','profile','experiments',
                     'targetInput','toleranceInput','edgeHeatmap','meshCanvas',
                     'radialFactor','heatView','heatMode','heatScale','heatRejected',
                     'pauseLive','sampleWindow','themeToggle','motionToggle','orbitToggle'):
            self.assertIn('id="'+name+'"',html)
        self.assertIn('/api/export.csv',html)
        self.assertIn('read-only',html.lower())

    def test_assets_are_local_and_visual_motion_can_be_disabled(self):
        html=(WEB_DIR/'index.html').read_text(encoding='utf-8')
        css=(WEB_DIR/'dashboard.css').read_text(encoding='utf-8')
        js=(WEB_DIR/'dashboard.js').read_text(encoding='utf-8')
        self.assertNotIn('https://',html)
        self.assertIn('prefers-reduced-motion',css)
        self.assertIn('body.no-motion',css)
        self.assertIn('displayPaused',js)
        self.assertIn('latestRemoteState',js)
        self.assertIn('chartRows()',js)
        self.assertIn('rowMedians',js)
        self.assertIn('No axial profile',js)
        self.assertIn('Pixel Only',html)

    def test_new_server_is_read_only_and_exposes_version(self):
        store=DashboardStore(max_rows=25)
        store.publish(mode='continuous',calibration='Working Calibration',measurements=[
            {'timestamp':'2026-10-14T12:30:01','elapsed_s':0.,'source':'continuous',
             'left_mm':1.75,'right_mm':1.75,'f_direct_mm':1.75,
             'edge_columns_left':[62.1,62.2], 'status':'VALID_MM'},
            {'timestamp':'2026-10-14T12:30:02','elapsed_s':.4,'source':'continuous',
             'left_mm':None,'right_mm':1.75,'f_direct_mm':None,
             'left_pixel_width':62.4,'status':'PIXEL_ONLY'}])
        server=LanDashboardServer(store,host='127.0.0.1',port=0)
        server.start()
        try:
            url='http://127.0.0.1:'+str(server.port)
            self.assertIn('14.7.2',json.load(urlopen(url+'/api/health'))['version'])
            state=json.load(urlopen(url+'/api/state'))
            self.assertEqual(state['measurements'][1]['status'],'PIXEL_ONLY')
            self.assertIsNone(state['measurements'][1]['f_direct_mm'])
            csv=urlopen(url+'/api/export.csv').read().decode()
            self.assertIn('PIXEL_ONLY',csv)
            self.assertIn('left_edge_col_00_px',csv)
            self.assertIn('62.1',csv)
            html=urlopen(url+'/').read().decode()
            self.assertIn('ADVANCED MEASUREMENT CONSOLE',html)
            self.assertIn('Auto orbit',html)
            self.assertIn('no-store',str(urlopen(url+'/dashboard.js').headers).lower())
            with self.assertRaises(HTTPError) as ctx:urlopen(Request(url+'/api/state',data=b'{}',method='POST'))
            self.assertEqual(ctx.exception.code,405)
        finally:
            server.stop()

    def test_pi_files_stay_independent(self):
        root=Path(__file__).resolve().parents[1]
        p=root/'filament_inspector'/'ui'/'app.py'
        text=p.read_text(encoding='utf-8')
        self.assertIn('from filament_inspector.ui.dashboard import DashboardUI',text)
        self.assertIn('from filament_inspector.acquisition.roi import',text)
        self.assertIn('from filament_inspector.web.server import',text)
        self.assertIn('class FilamentInspectorApp',text)
        self.assertTrue((root/'filament_inspector'/'camera.py').is_file())


if __name__=='__main__':unittest.main()

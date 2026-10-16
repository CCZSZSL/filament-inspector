"""Boundaries introduced by the modular refactor."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class FrameworkTests(unittest.TestCase):
    def run_python(self,code,env=None):
        return subprocess.check_output([sys.executable,'-X','utf8','-c',code],cwd=ROOT,env=env,text=True).strip()

    def test_measurement_imports_and_crossing_do_not_load_tk(self):
        code='''
import sys, importlib.abc, pkgutil, importlib
class NoGUI(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.startswith(('tkinter','filament_inspector.ui','PIL.ImageTk','picamera2','libcamera')):
            raise AssertionError('Unexpected GUI/camera dependency: '+fullname)
sys.meta_path.insert(0,NoGUI())
import filament_inspector.algorithms as algorithms
import filament_inspector.calibration as calibration
for package in [algorithms,calibration]:
    for module in pkgutil.iter_modules(package.__path__,package.__name__+'.'):
        importlib.import_module(module.name)
import numpy as np
from filament_inspector.algorithms.silhouette import _half_crossing
result=_half_crossing(np.r_[np.full(25,40.),np.full(25,180.)],25,inside_below=True)
assert result is not None
print('ok')
'''
        self.assertEqual(self.run_python(code),'ok')

    def test_writable_paths_follow_explicit_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env={**os.environ,'FILAMENT_INSPECTOR_DATA_DIR':directory}
            code='''
from filament_inspector.paths import DATA_ROOT
from filament_inspector.ui.local_calibration import AUTOSAVE
from filament_inspector.ui.position import AUTOSAVE_1467
from filament_inspector.ui.experiments import OUTPUT_ROOT
assert AUTOSAVE.parent == DATA_ROOT/'config'
assert AUTOSAVE_1467.parent == DATA_ROOT/'config'
assert OUTPUT_ROOT.parent == DATA_ROOT
print(DATA_ROOT)
'''
            self.assertEqual(Path(self.run_python(code,env)),Path(directory).resolve())

    def test_cli_help_does_not_start_gui(self):
        result=subprocess.run([sys.executable,'-m','filament_inspector','--help'],cwd=ROOT,text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Filament Inspector host application',result.stdout)

    def test_compatibility_host_launcher_uses_current_class(self):
        from filament_diameter_ui_v1472 import FilamentUIV1472
        from filament_inspector.ui.app import FilamentInspectorApp
        self.assertIs(FilamentUIV1472,FilamentInspectorApp)

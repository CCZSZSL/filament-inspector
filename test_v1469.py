"""Offline regression of axisymmetric dashboard model (no camera/server needed)."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from filament_profile_v1469 import ProfileRecorder, radius_mesh_sections, row_measurements


def add(r, t, f=1.75, source='continuous', feed=True, speed=20):
    return r.append(at_s=t,timestamp=f'{t}',source=source,left_mm=f,
             right_mm=f, f_direct_mm=f, delta_lr_mm=0 if f is not None else None,
             feed_enabled=feed,speed_mm_s=speed,status='VALID_MM' if f is not None else 'PIXEL_ONLY')


class ModelTests(unittest.TestCase):
    def test_actual_timestamps_not_interval(self):
        r=ProfileRecorder();add(r,100);add(r,102.4)
        self.assertAlmostEqual(r.samples[1].axial_mm,48.0)
        self.assertAlmostEqual(r.samples[1].elapsed_s,2.4)

    def test_motion_test_cannot_become_surface(self):
        r=ProfileRecorder()
        add(r,1,source='motion');add(r,3,source='motion')
        self.assertIsNone(r.samples[0].axial_mm)
        self.assertEqual(radius_mesh_sections(r.samples)[0],[])

    def test_feed_off_cannot_become_surface(self):
        r=ProfileRecorder();add(r,1,feed=False);add(r,3,feed=False)
        self.assertIsNone(r.samples[-1].axial_mm)

    def test_missing_frame_is_gap(self):
        r=ProfileRecorder()
        add(r,1,1.75);add(r,2,None);add(r,3,1.76);add(r,4,1.76)
        segments,base=radius_mesh_sections(r.samples)
        self.assertEqual(list(map(len,segments)),[1,2])
        self.assertAlmostEqual(base,(1.75+1.76*2)/3)
        self.assertEqual(r.samples[1].status,'PIXEL_ONLY')
        self.assertAlmostEqual(r.samples[3].axial_mm,60)

    def test_no_join_after_feed_disabled_or_reset(self):
        r=ProfileRecorder();add(r,1);add(r,2)
        add(r,3,feed=False)
        add(r,4);add(r,5)
        segs,_=radius_mesh_sections(r.samples)
        self.assertEqual(len(segs),1)
        self.assertEqual(segs[0][0][0],0)
        r.reset();add(r,100);self.assertEqual(r.samples[0].axial_mm,0)

    def test_reject_bad_speed_without_recording(self):
        r=ProfileRecorder()
        with self.assertRaisesRegex(ValueError,'speed'):
            add(r,1,speed=0)
        self.assertFalse(r.samples)

    def test_reject_backwards_clock(self):
        r=ProfileRecorder();add(r,5)
        with self.assertRaisesRegex(ValueError,'nonmonotonic'):
            add(r,4)

    def test_visual_exaggeration_does_not_change_physical_values(self):
        r=ProfileRecorder();add(r,1,1.74);add(r,2,1.76)
        mesh1,mean1=radius_mesh_sections(r.samples,exaggeration=1)
        mesh20,mean20=radius_mesh_sections(r.samples,exaggeration=20)
        self.assertEqual(mean1,mean20)
        self.assertEqual(mesh1[0][0][2],mesh20[0][0][2])
        self.assertNotEqual(mesh1[0][0][1],mesh20[0][0][1])

    def test_csv_import_keeps_invalid_but_no_axial(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'frames.csv'
            with file.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['timestamp','test','left_diameter_mm',
                    'right_diameter_mm','f_direct_mm','delta_lr_mm','frame_status'])
                w.writeheader()
                w.writerow(dict(timestamp='2026-10-14T20:51:20+11:00',test='motion',
                    left_diameter_mm=1.751,right_diameter_mm=1.75,f_direct_mm=1.7505,
                    delta_lr_mm=0.001,frame_status='VALID_MM'))
                w.writerow(dict(timestamp='2026-10-14T20:51:23+11:00',test='motion',
                    left_diameter_mm='',right_diameter_mm=1.75,f_direct_mm='',
                    delta_lr_mm='',frame_status='PIXEL_ONLY'))
            r=ProfileRecorder();self.assertEqual(r.from_csv(file),2)
            self.assertAlmostEqual(r.samples[1].elapsed_s,3)
            self.assertEqual(r.samples[1].status,'PIXEL_ONLY')
            self.assertIsNone(r.samples[0].axial_mm)
            self.assertIsNone(r.samples[1].f_direct_mm)
            output=Path(temp)/'export.csv';r.save_csv(output)
            with output.open() as f:
                self.assertEqual(len(list(csv.DictReader(f))),2)

    def test_old_continuous_csv_compatibility(self):
        left,right,fused,delta=row_measurements({'left_mm':'1.74','right_mm':'1.76',
                                    'f_direct_mm':'','delta_lr_mm':''})
        self.assertEqual((left,right),(1.74,1.76))
        self.assertAlmostEqual(fused,1.75)
        self.assertAlmostEqual(delta,.02)

    def test_limited_memory_and_downsampling(self):
        r=ProfileRecorder(max_samples=30)
        for i in range(50):add(r,i+1,1.75+i*.0001)
        self.assertEqual(len(r.samples),30)
        segments,_=radius_mesh_sections(r.samples,max_sections=10)
        self.assertLessEqual(sum(map(len,segments)),11)


class GuiSmokeTests(unittest.TestCase):
    def test_inherited_interface_and_graphs_open(self):
        # Must run within xvfb or on a desktop; skipped if headless.
        try:
            from filament_diameter_ui_v1469 import FilamentUIV1469
            win=FilamentUIV1469()
        except Exception as exc:
            if 'display' in str(exc).lower():self.skipTest('No Tk display')
            raise
        try:
            self.assertTrue(hasattr(win,'exp_static_button'))
            self.assertTrue(hasattr(win,'exp_motion_button'))
            self.assertTrue(hasattr(win,'continuous_button'))
            self.assertTrue(hasattr(win,'profile_feed'))
            win._profile_open();win.update()
            self.assertTrue(win.profile_window.winfo_exists())
            win.profile_recorder.reset()
            add(win.profile_recorder,1,1.74)
            add(win.profile_recorder,2,1.76)
            win._profile_render();win.update()
            self.assertGreater(len(win.profile_canvas_d.find_all()),5)
            self.assertGreater(len(win.profile_canvas_lr.find_all()),2)
            self.assertGreater(len(win.profile_canvas_3d.find_all()),5)
            win._exp_set_buttons(True)
            self.assertNotIn('disabled',win.profile_open_button.state())
            win._exp_set_buttons(False)
            win._profile_close()
        finally:
            win.destroy()


if __name__=='__main__':unittest.main()

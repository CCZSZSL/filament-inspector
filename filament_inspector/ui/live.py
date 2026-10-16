#!/usr/bin/env python3
"""v14.3.1 live preview and manual exposure: live Pi camera stream + fullres capture + legacy UI."""
from pathlib import Path
from tkinter import ttk
import cv2
from filament_inspector.ui.silhouette import SilhouetteThreeViewUI
from filament_inspector.ui.camera_preview import LivePanel


class LiveThreeViewUI(SilhouetteThreeViewUI):
    def _build_ui(self):
        super()._build_ui()
        self.title("Filament Diameter v14.3.1 | Live Focus & Exposure")
        self.live_panel = None
        base = self.adv_button.master
        heading = base.grid_slaves(row=0, column=0)
        for label in heading:
            if isinstance(label, ttk.Label):
                label.configure(text="Filament Inspector v14.3.1")
        first_section = base.grid_slaves(row=2, column=0)[0]
        ttk.Button(first_section, text="LIVE VIDEO • Connect Raspberry Pi",
                   command=self._open_live).grid(row=3, column=0, sticky="ew", pady=(8, 0), ipady=6)
        self._bind_controls_wheel_recursive(first_section)
        self.v14_hint.set("Video phase: Open LIVE VIDEO, focus once/lock, capture background, then capture current frame. Measurement remains on native full-resolution frames.")

    def _open_live(self):
        if self.live_panel and self.live_panel.winfo_exists():
            self.live_panel.lift()
        else:
            self.live_panel = LivePanel(self)

    def _clear_live_background(self):
        """Do not permit stale difference calculations after a focus change."""
        self.background_path = None
        self.background_cv_raw = None
        self.background_cv = None
        self.background_info_var.set("No valid background: recapture after focus")
        self.view_results.clear()
        self.engines.clear()
        self.last_detected.clear()
        self._reset_results_table()
        self._refresh_v14_status()
        self._redraw()

    def _load_background_from_live(self, path):
        """Equivalent of v13 background import without opening file chooser."""
        raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if raw is None:
            raise ValueError(f"Cannot load background: {path}")
        image = cv2.rotate(raw, cv2.ROTATE_90_CLOCKWISE)
        if self.image_cv is not None and image.shape[:2] != self.image_cv.shape[:2]:
            raise ValueError("Background and current image must use identical sensor frame sizes")
        self.background_path = Path(path)
        self.background_cv_raw = raw
        self.background_cv = image
        self.background_info_var.set(f"Live background: {path.name} | 90° CW")
        self.defect_mask_roi = None
        self.defect_background_gray = None
        self.defect_mask_coverage = None
        self.view_results.clear()
        self.engines.clear()
        self.last_detected.clear()
        for view in self.views.values():
            view["previous_y"] = None
        self._reset_results_table()
        self._refresh_v14_status()
        self._redraw()


if __name__ == "__main__":
    LiveThreeViewUI().mainloop()

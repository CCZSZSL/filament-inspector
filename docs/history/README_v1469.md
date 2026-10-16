# Filament Inspector v14.6.9 — Diameter Dashboard & Approximate 3D Profile

## Function and scope

**Upgrade from the user-tested v14.6.8 Stable Baseline.** This release introduces a **separate, nonintrusive dashboard** while keeping the v14.6.8 GUI, experiment module, v14.6.7 detector, calibration, dynamic ROI, seeds, dual mirror, 40-column robust fusion, virtual F-Direct, continuous mode and Raspberry Pi Camera Server **unchanged**. No ECC, Kalman filter, or change to the physical diameter estimator. The former GUIs remain runnable.

The three new displays are:

1. **Diameter Trend (mm)**: Left, Right and F-Direct `(Left + Right)/2`, versus actual elapsed host time. A missing calibrated mm reading is a gap.
2. **Inter-View Diameter Difference (mm)**: `ΔLR = abs(Left - Right)` versus elapsed time. This is **not** true roundness, ovality or cross-sectional profile.
3. **Axisymmetric 3D Diameter Profile**: a visually extruded object constructed by **assuming all cross-sections are circular** with radius `F-Direct/2`, with approximate axial position `z[i] = z[i-1] + assumed_speed_mm_s * (host_monotonic_time[i] - host_monotonic_time[i-1])`. This is **not real 3D surface roughness** or a Ra/Rq/Rz metrology measurement. A user-controlled *visual-only* radial deviation amplification of 1×, 5×, 10×, 20×, 50× or 100× makes subtle variations visible. Exported measured mm values are never changed by the display amplification.

**Absolute caveats:** For a known line speed, elapsed time is approximated from the Mac's **measurement completion time**, not a time-synchronized camera exposure or optical encoder. Capture/download/processing jitter, filament slip and actual line speed changes cause **axial registration error**. Thus the axis is labelled APPROXIMATE and is appropriate for an exploratory picture, not accurate longitudinal defect metrology. Unknown shape **between sample positions** is illustrated by interpolation, not measured. Check the displayed **median sample pitch**; a 20 mm/s line at ~2 seconds per sample covers about 40 mm per sample, far too sparse for small scratches or roughness.

If any view enters **Pixel Only** (for instance when left Y exceeds the existing ±20 px gate), keep the raw pixel data in the original experiment CSV and leave a gap in the dashboard's calibrated diameter plot. Never invent mm by pixel-only extrapolation, and never bridge such gaps in 3D.

## Mac host terminal (NOT Pi and NOT Docker)

1. Unzip `filament_live_v1469.zip` somewhere, such as `~/Downloads`.
2. Install/run:

```bash
cd ~/Downloads/filament_live_v1469
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1469.py
```

The existing Pi server command, server protocol and SSH connection remain exactly as in v14.6.8. Do not replace your installed Pi server. Use **Load Setup JSON** if the saved calibration resides in your previous v14.6.8 install directory; the new ZIP cannot automatically access that external state. Take a new EMPTY background with unchanged capture settings.

## Using the new panel

1. Use the existing procedure to configure and calibrate mirrors (ROI, seeds, locked imaging conditions, empty background).
2. Click **OPEN DASHBOARD — DIAMETER + ΔLR + 3D PROFILE**. The dashboard can be opened during a live experiment; it does not modify the detector.
3. Start normal **Continuous Measurement** or Static/Motion Experiment. The diameter and `ΔLR` curves update after completed measurements. The number of samples is indicated in the original GUI.
4. For *real longitudinal feeding only*, before starting Continuous Measurement, enter the physically measured **Speed (mm/s)** and tick **Actual AXIAL feed active**. This resets dashboard history to avoid stitching separate runs. While enabled, speed is locked until you untick it. Static and lateral-motion experiments **never** acquire any computed axial length, regardless of the toggle state.
5. On the **Axisymmetric 3D** tab, the surface appears after at least two valid-mm frames in a continuous, explicitly enabled axial-feed session. The positions are approximate, and missing mm samples interrupt its surface segments. Change the visual exaggeration without affecting logged physical measurements.
6. **Load CSV** allows analysis of a previous v14.6.8 experiment (`frames.csv`) or v14.6.7 continuous log, including invalid frames, but **never** auto-infers axial length from an imported CSV (feed speed/motion is not recorded reliably). **Save Dashboard CSV** exports the dashboard's time-series records and optional approximate axial positions. It is a supplemental CSV and does **not** replace v14.6.8 experiment CSV or existing continuous log.

The dashboard keeps up to 5000 samples in memory to avoid uncontrolled GUI growth; the original v14.6.7 continuous CSV and v14.6.8 experiment CSV are **not affected** by this dashboard memory bound. Saving a dashboard CSV saves the currently retained dashboard samples.

## Strict naming

- `F-Direct`: **virtual mean** of Left and Right, **NOT** an independent direct optical view.
- `ΔLR`: **Inter-View Diameter Difference**, not roundness.
- `3D Diameter Profile`: **Axisymmetric model** based on circular sections, not surface microtopography.
- `Surface Roughness Ra/Rq/Rz`: **not measured**. Physical surface height traces and suitable spatial resolution/filtering are required (e.g. ISO 21920-2:2021; NIST *Introduction to Surface Finish Metrology*, Vorburger, 2010).
- Colors: no RGB collection or color analysis in this release; GRAY8 camera server is untouched.

## Tests

**Mac host terminal:**

```bash
python -m unittest -v test_v1469 test_v1468 test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

The test suite includes a Tk GUI smoke test, CSV import/export, axial time integration, no false 3D length for static/motion, invalid-mm gaps, different-run separation, and model visual-only magnification. These are **offline** checks; the user must still verify the live Raspberry Pi + Camera Module 3 hardware setup and whether actual feed speed matches the assumed value.

**Fallback:** Run the prior `python filament_diameter_ui_v1468.py` or `python filament_diameter_ui_v1467.py` inside this package; these modules are copied byte-for-byte without changes. Keep the original v14.6.8 ZIP separately, too.

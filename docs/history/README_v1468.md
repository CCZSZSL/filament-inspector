# Filament Inspector v14.6.8 — Position-Induced Error Analysis

**Upgrade basis:** exact, stable v14.6.7 sources. New files: `filament_diameter_ui_v1468.py`, `filament_experiments_v1468.py`, `test_v1468.py`, and this README. The original v14.6.7 GUI and all legacy detector/fusion/calibration modules remain available, with their source code **unchanged**. **Raspberry Pi server `pi_live_camera_server_v145.py` is unchanged.** No ECC, no Kalman filter, no new diameter estimation formula.

## 1. Installation / launch — **Mac host terminal (Mac 主机终端)**

Open Terminal **on the Mac**, **not** Pi SSH, **not** Docker. Extract the ZIP and run:

```bash
cd ~/Downloads/filament_live_v1468  # change to your real extraction location
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m tkinter              # optional: verify Tkinter opens a test window
python filament_diameter_ui_v1468.py
```

For later launches, just:

```bash
cd ~/Downloads/filament_live_v1468
source .venv/bin/activate
python filament_diameter_ui_v1468.py
```

Or double-click `START_v1468_Mac.command` once a working `.venv` is present. The original camera server and Mac SSH forwarding/network route to `http://127.0.0.1:8765` are the **same as v14.6.7**. Do **not** restart or replace your working Pi server simply because the GUI was upgraded. Ensure the Mac can reach it before starting experiments.

**Fallback / 回退:** From this same package, stop the experiment (or close the v14.6.8 GUI), then run:

```bash
cd ~/Downloads/filament_live_v1468
source .venv/bin/activate
python filament_diameter_ui_v1467.py
```

This launches the untouched v14.6.7 implementation. An independent copy of the prior v14.6.7 ZIP should also be kept as the original stable release. The new package never deletes an existing calibration. Its own `config/dual_mirror_v1467.json` is in its extracted folder; if your stable configuration lives elsewhere, use **Load Setup JSON** from your existing v14.6.7 folder. Load a full-resolution frame to apply restored settings, then take a new empty background at unchanged exposure/focus.

## 2. Experimental workflow / 实验流程

**Before both tests:**

1. Connect using the normal Camera controls. Maintain **4608×2592 8-bit grayscale PNG** snapshots. The MJPEG preview is **not** used for measurement.
2. Ensure an **EMPTY background**, valid left/right mirror ROIs with the **same 40-column fixed X band**, learned seeds, and any existing per-mirror working calibration.
3. Complete one normal `Capture + MEASURE 2 mirrors` and check both mirror results.
4. For the primary error-discovery experiment **uncheck `Enable compensated mm inside learned Y range`**. With v14.6.7's optional compensation ON, a real position effect can be partially hidden. Check later with compensation ON as a separate comparison test. When compensation is off, the inherited ±20 px base-calibration gate still applies; frames outside it remain **Pixel Only**, with the measured pixel widths retained.
5. Set **Interval after each frame** to **0.5–30 s**, e.g. 2.0. This is a *wait after finishing the previous capture + measurement*, **not** a guaranteed camera frame rate.

**START STATIC TEST — 50 FRAMES:** Keep filament in its natural, stationary operating position. Click the button once. Captures run automatically to 50 attempts. The panel shows `attempts / target`, valid-mm count, and the latest frame status.

**START MOTION TEST — 100 FRAMES:** Allow realistic mild **lateral** motion throughout the 100-frame run; do not intentionally alter focus, exposure, or light. **Both mirror Y positions are logged as detected** (no forced smoothing), including frames without valid mm. Speed/displacement are **not** inferred from Y alone; each mirror may invert physical movement.

**STOP TEST:** Stops scheduling new frames. If an HTTP snapshot is in progress, STOP waits for that one capture to settle, records the final attempt and then closes CSV/reports. HTTP fetch happens in a background thread; the existing v14.6.7 image decode, image display and measurement callbacks run on the Tk main thread and can briefly pause responsiveness while processing a 12 MP image. Only one test can run at a time; the normal continuous camera button and other GUI action buttons are disabled during the test. If the app is closed mid-request, that attempted frame is saved as an explicit interrupted row.

Do **not** move or resize the mirror ROIs, change focus, change exposure or capture a new empty background during a test. Camera epoch changes abort the run instead of extrapolating calibration.

## 3. Files written / 输出文件

Every experiment immediately creates a new folder under:

`measurements_v1468/YYYYMMDD_HHMMSS_microseconds_static_50frames/` or
`measurements_v1468/YYYYMMDD_HHMMSS_microseconds_motion_100frames/`.

File | Content
---|---
`frames.csv` | **Every attempted capture**, including failed HTTP, incomplete PNG, Pixel Only, partially measured frames; appended/flushed once per frame
`metadata.json` | Test type, start time, requested frames, interval, exact ROI, compensation on/off, camera settings, background epoch, format, and provenance
`report.json` | Machine-readable means, sample SDs, ranges, correlations, invalid rows, outlier references, valid/attempt counts
`report.txt` | Human-readable report shown by **VIEW TEST RESULTS**
`outlier_flags.csv` | All attempt numbers, with flagged signals; **never** silently deletes outliers from `frames.csv`

The unchanged client saves **the latest** full-resolution image as `captures_live_v145/current_latest.png` and associated metadata. It does **not** save 50/100 full PNGs individually. Thus disk usage remains manageable, but only CSV time series and latest image are retained. If all raw image files are required for further reprocessing, that is a separate image-archiving feature **not included** here.

### CSV key columns

`timestamp`, `attempt`, `test`, `capture_status`, `frame_status`, `failure_reason`, `snapshot_bytes`, camera epochs, `left_y_px`, `left_pixel_width`, `left_diameter_mm`, `left_valid_columns`, `left_quality`, `left_edge_fit_residual_px`, `left_calibration_status`, `left_position_mode`, `left_failure_reason`; corresponding `right_…` fields; `f_direct_mm`, `delta_lr_mm`, `signed_lr_mm`, and `f_direct_status`.

- Pixel width is **the existing 40-column robust fused normal-edge width (px)**, **not** a manual or new edge detector. If the inherited algorithm supplies a pixel-domain fused value, it is saved directly. If it supplies fused millimetres, the pixel width is recovered using its **applied per-view scale** as `fused_mm / scale_mm_per_px` (up to floating-point roundoff). This conversion makes no additional corrections.
- `frame_status=VALID_MM`: both mirrors and virtual F-Direct have valid mm. `PIXEL_ONLY`: both views have pixel widths, but one/both cannot be converted to mm. `PARTIAL_OR_FAILED` and `INVALID`: at least one requested quantity failed or a processing exception occurred.
- Empty `diameter_mm` is intentionally not written as 0. A failed snapshot is still a row with a reason. `Quality` is the v14.6.7 confidence **heuristic**, **not** probability of metrology accuracy.
- Y is **90° clockwise rotated working-image pixel Y**, not mm and not the Pi's unrotated sensor coordinate.
- **F-Direct is virtual:** `(Left_mm + Right_mm)/2`, valid only when both calibrated results exist. `delta_lr_mm=abs(Left-Right)` measures mirror disagreement **not roundness**.

## 4. Report interpretation / 统计说明

- **Static Repeatability:** per-channel mean, **sample SD (`ddof=1`)**, min/max and `range=max-min` across valid observed measurements. Consistent stationary data quantify repeatability, not absolute accuracy.
- **Motion-Induced Variation:** calculate the same spread on motion data, then compare `f_direct_mm` and each mirror's SD/range against the corresponding static test. A larger motion SD may indicate position dependence, but may also contain changing background/contrast or real filament geometry variation.
- **Y vs Pixel Width** and **Y vs Diameter:** per-mirror Pearson `r`, linear regression slope (pixels width or mm **per Y pixel**), intercept and `r²`. The report also correlates **each mirror's Y** separately with the virtual F-Direct mm. F-Direct has **no direct physical pixel width**.
- **Left–Right Consistency:** `delta_lr_mm` absolute difference and `signed_lr_mm=Left-Right` have their own summary stats.
- **Invalid/Outlier:** all invalid frames are listed with reasons; possible outliers use `|x − median| > 3.5 × 1.4826 × MAD` on each reported metric if at least five observations exist. Flags are **diagnostic only**; statistics and correlations are **unfiltered**. When MAD=0, any measurable deviation beyond numeric epsilon may be flagged. One outlier may be a real transient, not necessarily bad data.

**Causal limitation:** Motion, time, geometry, brightness and actual filament diameter can vary together. A correlation between Y and width is evidence of an association, **not proof** that physical lateral displacement alone causes scaling error. To test reproducibility, compare repeated static/motion runs with identical settings and preferably a fixed-diameter certified reference at several controlled positions. A 1.75 mm nominal filament is only a working reference until externally validated.

## 5. Regression and offline simulation tests

**Mac host terminal** (within the new package folder, after activation):

```bash
python -m unittest -v test_v1468 test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

Tested during development under Linux with virtual Tk display: **58 / 58 PASS** (48 unchanged baseline tests and 10 new tests), including simulated **complete 50 static + 100 motion** iteration runs, actual 4608×2592 **synthetic GRAY8 PNG** validation through the inherited camera snapshot client, simulated HTTP/PNG failures, Pixel Only, CSV flush/recovery, button mutual exclusion, stop while capture in flight, Y correlations, and robust outlier flags. Tests do **not** substitute for real Pi 4 + IMX708 / two mirrors / moving filament hardware measurements, which have **not** been performed in this environment.

## 6. File ownership and rollback guarantee

- **Original untouched:** `filament_diameter_ui_v1467.py` (stable fallback), `filament_diameter_ui_v1466.py`, `filament_dynamic_fusion_v1463.py`, `filament_seed_guided_v1462.py`, `filament_f_direct_fusion_v1463.py`, `filament_position_compensation_v1467.py`, **`pi_live_camera_server_v145.py`**, and all other legacy files.
- **New GUI orchestration:** `filament_diameter_ui_v1468.py`. Performs acquisition on a background network thread, polls results via Tk `after`, reuses original `current` decode and original v14.6.7 `_v14_process_all()` exactly, and writes per-attempt logs.
- **New analysis:** `filament_experiments_v1468.py`. No camera commands or edge calculations.
- **New tests:** `test_v1468.py`.

Run the old file `filament_diameter_ui_v1467.py` (or re-extract the independent original v14.6.7 ZIP) if any new experiment function behaves unexpectedly. **No changes to the Pi are required to roll back.**

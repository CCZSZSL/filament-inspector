# Filament Inspector v14.6.7 — Y-Offset Compensation + One-Button Continuous Measurement

Built **on v14.6.6**, retaining the **v14.6.3-F Dual Mirror / virtual F-Direct** metrology. The physical Direct View is not measured. Raspberry Pi Camera Server **unchanged**.

## Main changes

1. **Y-Offset Compensation**: independent per-mirror empirical lookup of reference pixel diameter versus the **actual tracked Y coordinate**, learned from the *same* filament held at center, upper and lower positions. The 40-column robust pixel measurement remains unchanged.
2. **Visible Start/Stop Continuous Measurement button**, positioned immediately below the camera panel. No need to locate the older check box. Each iteration captures a fresh, native-resolution 4608×2592 GRAY8 PNG. Adjustable **0.5–30 seconds** between acquisitions; actual sampling period additionally includes capture and processing time.
3. **CSV history**: each continuous run records a new `measurements_v1467/continuous_YYYYMMDD_HHMMSS_ffffff.csv`, including timestamp, F-Direct mm, left/right mm, ΔLR, tracked Y, selected scale, accepted columns and scale mode. Failed/pixel-only views use empty mm fields.
4. **Profile safety**: new Y-position anchors use 10 new images and the **same** v14.6.6 per-frame quality checks. Adding both mirror anchors is atomic; unsuccessful acquisition preserves all prior points and both base scales. No scale extrapolation outside the observed Y-position range. Changing a mirror's base calibration discards its old positional profile. Saved position curves are validated before loading.
5. **Legacy setup compatible**: `Load Setup JSON` accepts an existing `v14.6.6` dual-mirror setup, including ROIs, seeds and base scales. The new position profile is optional.

## Installation — Mac host terminal (not Pi SSH, not Docker)

Extract `filament_live_v1467.zip` next to the old release and enter its folder (substitute your actual folder if different):

```bash
cd ~/filament_live_v1467
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1467.py
```

Future runs only require the `cd`, `source`, and `python filament_diameter_ui_v1467.py` lines. Ensure Tkinter exists (`python3 -m tkinter`). Keep the same existing SSH tunnel to `http://127.0.0.1:8765`, Raspberry Pi camera server, focus and exposure.

## Preserve your successful v14.6.6 calibration

Do **not** delete the old program or its `config/dual_mirror_v1466.json`.

1. In the new v14.6.7 GUI, press **Load Setup JSON**, select the `config/dual_mirror_v1466.json` located under the **old v14.6.6** folder. Alternatively, use a separate JSON saved from v14.6.6 with **Save Setup JSON**.
2. Capture or load a valid 4608×2592 full-resolution frame to finish restoring settings. `Load Setup JSON` does **not** command the Pi to change its camera settings.
3. Capture a new **EMPTY background** under the same camera/LED settings if a valid one has not already been captured during this GUI session. ROIs, seed models and base scales should be restored.
4. Check the ordinary `Capture + MEASURE 2 mirrors` before doing any position training.

v14.6.7 saves its own auto settings at `config/dual_mirror_v1467.json` (v14.6.6's original file stays unchanged). Manual Save Setup JSON also includes per-mirror Y profiles when present.

## Two problems: tracking versus position bias

**ROI tracking** was already present in v14.6.6: the seed-guided detector searches each mirror's whole Y ROI, follows the current stripe, then applies the existing subpixel 50% intensity crossings. Simply moving an ROI box would **not** solve a change of optical scale with position.

**Position-dependent scale** is new. For a reference diameter `Dref=1.75000 mm`, position anchor `i` on mirror `j` stores:

```
y[j,i]  = median tracked center Y, in rotated working image pixels
p[j,i]  = median 40-column robust fused pixel diameter (10 independent PNG captures)
S[j,i]  = Dref / p[j,i]  [mm/px]
```

For a new center `y`, find the two adjacent anchor positions and **linearly interpolate the expected reference pixel diameter**:

```
p_ref,j(y) = linear_interpolation((y_low,p_low),(y_high,p_high),y)
S_j(y)     = Dref / p_ref,j(y)
D_j        = P_j * S_j(y)  # P_j: current robust 40-column fused PIXEL diameter
D_F        = (D_left + D_right) / 2  # only after BOTH become mm
Delta_LR   = abs(D_left - D_right)  # NOT roundness
```

Interpolating the **reference pixel width first**, then taking its reciprocal, avoids a small error from linearly interpolating `mm/px` when the pixel-width trend itself is approximately linear.

### Learning an operational motion profile

1. At the current normal **CENTER** filament position, keep your existing v14.6.6 successful base calibrations (e.g., 1.75000 mm nominal reference). This center position becomes the first anchor on **each** mirror.
2. Expand both mirror **search ROI Y bounds** enough to include the expected upper and lower image positions **and exterior background**. Keep their fixed 40-pixel X bands unchanged, and avoid changing the optical setup.
3. Move the **same physical filament** visibly *UP* to a controlled upper position, **without sliding it lengthwise** or changing camera focus, exposure, LED lighting or background. Click **Record UPPER / LOWER Point (10 frames)**. A successful capture adds one independent anchor to each mirror.
4. Move the **same filament** visibly *DOWN* **past its original center** and click **Record UPPER / LOWER Point (10 frames)** again. Both mirror tracked Y values must bracket their own original baseline centers. The app uses the numeric Y values; it does not assume left/right optical mirror image orientation.
5. Keep **Enable compensated mm inside learned Y range** checked. Status should show `ACTIVE min…max Y px` for both sides. New images automatically select position-dependent scales. Additional anchors may be recorded along the range if useful.
6. In each new sampled location both mirror positions must differ from all existing anchors by **at least 6 image pixels**, and the 10 frames must satisfy quality/repeatability requirements (at least 8 inliers, ≥35/40 valid columns, median edge-fit residual ≤0.8 px, quality ≥0.65, CV ≤1.5%). A scale deviating >5% from the base is rejected to catch gross mis-detections.
7. Move the filament repeatedly between calibrated positions while **Start Continuous Measurement** is running, and check that `F-Direct` remains stable in the CSV history while Left/Right tracked Y values vary.

**Important:** Whether a physical upward/downward movement increases or decreases the displayed `Y` coordinate depends on the mirror; only the captured Y coordinate matters. The **middle** center and **both sides** must be observed for *each* mirror. If the actual movement causes *both* image coordinates to vary in an unexpected path or yields nearly identical Y values, this one-dimensional model is inadequate.

### Safe fallback

- If the correction is **not yet trained** or its checkbox is off, the original v14.6.6 **fixed local Scale** behavior remains, including its ±20 px validation gate.
- If the curve is active but the tracked filament is outside its observed position range (more than 1.5 px boundary tolerance), **Pixel Only** appears for that view and F-Direct mm is unavailable. No uncalibrated extrapolation is permitted.
- If the reference, camera focus, fixed X band or original resolution differs, corrected mm is rejected. Recalibrate where appropriate.
- `Clear Y-Offset Points` removes extra points but does **not** discard either v14.6.6 base Scale or Seed. `Calibrate Both Mirrors` updates base Scales and automatically invalidates old Y-position training.

**Optical limitation:** A camera coordinate `Y` cannot uniquely determine a three-dimensional out-of-plane shift (toward/away from the camera). Y compensation only corrects a **repeatable motion path where measured Y determines the local projection scale**. If physically different depths project to the same Y, use a mechanical filament guide / low-friction rollers, a telecentric optical system, an independent depth sensor, or a separately validated 2D/3D mapping. Also, position-trained correction is not a substitute for checking reflections/occlusions/background clipping. Using nominal high-quality 1.75 mm filament is adequate for an *internal working reference* at the project's chosen accuracy target but does not establish external traceability.

## Continuous measurement button

- After connecting, selecting ROIs/Seed, and acquiring a valid empty background, enter `Interval (s)` (try `2.0`).
- Press **▶ START CONTINUOUS MEASUREMENT**. It changes to **■ STOP CONTINUOUS MEASUREMENT**. Each iteration downloads a new native-resolution GRAY8 PNG then computes both mirrors and virtual F-Direct. It does **not** measure the reduced-resolution MJPEG preview.
- Press **■ STOP** to stop scheduling more captures. A previously issued capture may still finish. A CSV log is written automatically for each run.
- On focus/exposure changes or an invalidated background, continuous measurement stops and the button returns to START.
- The original `Capture + MEASURE 2 mirrors` is still available for a single new snapshot, and `MEASURE LEFT + RIGHT → F-DIRECT` continues to analyze the currently loaded image.

## Tests / compatibility

**Mac host terminal**, inside the new folder:

```bash
python -m unittest -v test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

This release passed **48/48** automated tests under a virtual Tk display (37 unchanged v14.6.6/regression tests + 11 new tests). Tests include position bracketing/interpolation/extrapolation, per-mirror mm-before-mean, stale-profile rejection, 10-frame rollback, JSON roundtrip, real GUI widget creation, continuous start/stop and CSV output. Hardware end-to-end measurements of moving filament on the actual Pi 4 + IMX708 / mirrors have **not** been run in this release.

## Core files

- **Run:** `filament_diameter_ui_v1467.py`
- **New position correction:** `filament_position_compensation_v1467.py`
- **Inherited base GUI/10-frame calibration:** `filament_diameter_ui_v1466.py`, `filament_flexible_calibration_v1466.py`
- **Existing metrology unchanged:** `filament_dynamic_fusion_v1463.py`, `filament_seed_guided_v1462.py`, `filament_f_direct_fusion_v1463.py`, `filament_silhouette_v142.py`
- **Existing Pi camera server unchanged:** `pi_live_camera_server_v145.py`
- **Tests:** `test_v1467.py` + all previous tests.

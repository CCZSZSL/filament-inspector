# Filament Inspector v14.6.6 — Simplified GUI + Flexible Calibration

**Entry point: `filament_diameter_ui_v1466.py` (Mac host).** This release is built on the user's `v14.6.3-F — Dual Mirror + F-Direct View` source. The Raspberry Pi `pi_live_camera_server_v145.py` is copied without modification and does not need replacement on the Pi. **No v14.6.4 ECC registration is used.**

## Mac host terminal — installation and launch

Unzip `filament_live_v1466.zip` and **in the Mac host terminal (NOT the Raspberry Pi SSH terminal or VS Code Docker container):**

```bash
cd ~/Downloads/filament_live_v1466
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1466.py
```

If you unzip elsewhere, replace the `cd` path with the actual extracted directory. macOS requires Python with functional Tkinter (`python3 -m tkinter` checks Tk support). `opencv-python`, `numpy`, and `Pillow` are installed from requirements. Close the GUI normally to save work.

### Camera and tunnel (unchanged)

Keep the **existing** Raspberry Pi camera server running as before. Set up the existing tunnel `127.0.0.1:8765`. Example in a **separate Mac host terminal**, only if your previous tunnel is not already running:

```bash
ssh -N -L 8765:127.0.0.1:8765 <pi-user>@<pi-host>
```

Replace `<pi-user>` and `<pi-host>` with your real username/host; never start two tunnels on the same port. The GUI defaults to `http://127.0.0.1:8765` and **9.750 LensPosition as an initial field value only**; the connected Pi's reported focus is authoritative, and loading a saved GUI setting does not lock or change the Pi automatically. Use **Set & Lock** explicitly if needed. Focus or exposure changes invalidate the background and require a fresh empty background image.

## GUI workflow

1. Connect the camera (connection is also attempted automatically at startup). Capture an **EMPTY background**, then insert the reference or test filament.
2. Click **Draw Left ROI**, select a tall region containing the left mirror projection with exterior background above/below it. Repeat **Draw Right ROI**. Both share one **40-column** horizontal X band, adjustable with **Shared X center / Apply X**. The physical Direct ROI is no longer used or displayed.
3. Choose **Left Mirror** and click **Learn Seed (NEW PNG)**. Draw **one small interior seed rectangle** in the freshly captured full-resolution frame. Repeat for the right mirror. These models are independent.
4. In **Calibration Panel**, enter any positive finite `Reference Diameter (mm)` smaller than 20, e.g. `1.75000`, `2.00000`, or a measured precise value. With the actual reference fixed in the field of view, click **Calibrate Left Mirror**, **Calibrate Right Mirror**, or **Calibrate Both Mirrors**.
5. Calibration automatically acquires **ten NEW 4608×2592 GRAY8 PNGs** (both views reuse the *same fresh 10 frames* in the combined command), and evaluates each snapshot using the **original unmodified v14.6.3 40-column fused-pixel estimator**. For a single-view calibration, the other mirror does **not** need to be configured.
6. Click **Capture + MEASURE 2 mirrors** (or **MEASURE LEFT + RIGHT → F-DIRECT** for the current image), or enable continuous capture. The result table displays **Left**, **Right**, and virtual **F-Direct**. Zoom/pan, live preview, autofocus/manual focus, manual exposure/gain, slice diagnostics, and seed failure diagnostics remain available.
7. Save and load the complete dual-mirror setup using **Save Setup JSON / Load Setup JSON**. The app also autosaves to `config/dual_mirror_v1466.json` and restores it on the next correctly sized full-resolution image. Calibration survives closing and reopening the GUI. Loading does **not** physically set the Pi camera parameters.

## Metrology and safety

**Measurement**

```text
P_L, P_R  = independent v14.6.3 40-column robust fused pixel widths
S_L       = reference_diameter_mm / median(accepted Left reference fused-pixel widths)
S_R       = reference_diameter_mm / median(accepted Right reference fused-pixel widths)
D_L       = P_L * S_L
D_R       = P_R * S_R
D_F       = (D_L + D_R) / 2                 [mm, only when both calibrated]
Delta_LR  = abs(D_L - D_R)                 [mm, NOT roundness]
```

The existing original-camera GRAY8 **50% intensity crossings**, sub-pixel edge localization, dynamic Y tracking, 40-column robust fusion, and per-view independent scaling remain the metrology path. **No average of the two pixel widths** is ever reported as F-Direct. Original photo metadata and full-resolution checks remain enforced.

**Calibration safety checks**

- Each of the ten new frames must independently pass **≥35/40 valid fused columns**, median edge-fit residual **≤0.8 px**, and detection quality heuristic **≥0.65** to be accepted.
- Require **at least 8/10 inlier frames**, robust frame-outlier filtering, an observed center displacement envelope of **20 px**, and frame repeatability CV **≤1.5%** (quality screens, **NOT** metrology accuracy guarantees).
- Each Scale stores reference diameter, sample count, robust reference pixel median, mm/px Scale, frame standard deviation in px and in mm, CV, valid columns, edge fit residual, time, camera settings, image size, and ROI location.
- For **Calibrate Both Mirrors**, the two updates are **atomic**: either both new Scale values are accepted or both prior values remain. Individual calibration failures never overwrite previously stored valid Scale values. An interrupted capture also leaves old scales intact.
- When uncalibrated, or when a valid local Scale cannot safely be applied (e.g., mismatched size, X band, camera focus metadata, or filament Y moving over 20 px from calibration center), show **Pixel Only**. F-Direct shows **unavailable** unless **both** views return valid current-frame millimetre measurements.
- **Working Calibration** is intentionally shown even if a nominal filament diameter was entered. Nominal 1.75 mm filament **does not establish ±0.01 mm absolute accuracy**. Repetition over ten frames reduces short-term noise, not reference bias, magnification bias, illumination bias or calibration traceability errors. **±0.01 mm absolute error is still a project goal, not a validated result.**

**Small targeted compatibility adjustment:** `filament_silhouette_v142.py` now uses the **actual detected filament center Y** (rather than the midpoint of a potentially tall *search* ROI) when checking whether a stored local Scale is still within its ±20 px validity area. This aligns the pre-existing scale gate with the already existing check in `measure_dynamic`. It does **not** modify 50%-crossing, edge positions, 40-column robust fusion, or pixel-to-millimetre multiplication. All original dynamic-fusion regression tests still pass.

## Saved setup JSON fields

`config/dual_mirror_v1466.json` (or any manual setup save):

- Top-level schema, 90° CW rotation, native size, shared X center, reference diameter, save timestamp, camera settings.
- `views.left`, `views.right`: ROI, seed model, independent `local_scale_calibration`, detector parameters. No Direct ROI, Direct K_ref or Direct homography in the saved schema.
- Per-scale record: `calibration_timestamp`, `camera_settings`, `calibration_quality_metrics`, `search_roi_xyxy_at_calibration`, `search_roi_center_xy`, `mm_per_px`, original size, declared reference diameter, `reference_type=working_calibration`.

For a previous `v14`/`v14.6.3-F` setup, the new loader accepts the **new v14.6.6 schema only** to prevent accidental import of obsolete physical Direct calibrations. Existing historical JSON remains on disk, unmodified; recreate ROIs/seeds and calibrate with v14.6.6, or perform a reviewed manual conversion.

## Tests

Run in **Mac host terminal**, from the extracted release directory:

```bash
python -m unittest -v test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

The suite tests reference inputs, invalid-column/reprojection/quality gates, 10-snapshot statistics, isolated and both-mirror calibrations, failure rollback, save/load, no-Direct-ROI GUI rendering and single-frame two-mirror fusion, plus original v14.6.3-F regressions. Tests involving Tk require a GUI display; test environments without displays may run them under `xvfb-run`.

**Validation limitations:** These are source-level, unit, synthetic-image, and headless Tk GUI tests. Real Pi 4/IMX708, physical mirror geometry, real sensor-noise repeatability and traceable ±0.01 mm **have not** been hardware validated in this build. Keep the previously working server and hardware configuration unchanged until verifying with your fixture.

## Release file guide

- `filament_diameter_ui_v1466.py`: **start this** (new simplified GUI, camera-handler compatibility, serialization).
- `filament_flexible_calibration_v1466.py`: robust, isolated ten-snapshot mirror-calibration logic and quality gates.
- `filament_dynamic_fusion_v1463.py`: original unchanged dynamic 40-column metrology.
- `filament_f_direct_fusion_v1463.py`: original independently calibrated mm fusion.
- `filament_seed_guided_v1462.py`: original independent one-time seed model.
- `filament_silhouette_v142.py`: original edge estimator with the small Y-validity-gate consistency fix above.
- `filament_live_integrated_v145.py`, `pi_live_camera_server_v145.py`: **unchanged** camera protocol/client class and Pi server; GUI overlays are in the new entry point.
- Other `filament_*.py` files: inherited compatibility imports, **not** alternative launch targets.
- `test_v1466.py`: v14.6.6 tests; accompanying `test_*.py`: original regressions.

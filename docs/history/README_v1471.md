# Filament Inspector v14.7.1 — ONE-ATTEMPT Fast ROI + Heatmap v2

**This is an OPTIONAL submission experiment, not a proven faster production release.** Keep the working `filament_live_v1470` folder and the existing Pi `pi_live_camera_server_v145.py` unchanged. If the ONE hardware test fails, **submit v14.7.0**. All v14.7.0 source files remain unmodified in the new package.

## What changed (and what did not)

- Mac entry point: `filament_diameter_ui_v1471.py`. Adds an **OFF-by-default** checkbox, `Use FAST ROI for Continuous Measurement` and one-click `STOP FAST / USE STABLE FULL FRAME`.
- Raspberry Pi NEW OPTIONAL server: `pi_live_camera_server_v1471.py`. Adds `GET /snapshot_roi.png?x0=N&width=64`; the original `/snapshot.png`, `/stream.mjpg`, `/status`, focus and exposure routes are intact. Original `pi_live_camera_server_v145.py` is still included and unchanged.
- On Mac, full-frame background / seed learning / calibration / static tests / motion tests / single manual captures still use **original full-resolution GRAY8 PNG**. ONLY the continuous measurement loop uses native-pixel ROI when you opt in.
- In the sensor's unrotated 4608×2592 image, the original 40-column measured region is a **horizontal sensor strip** because the WORKING image is rotated 90° clockwise. A 64-column WORKING X band means **64×4608 GRAY8 source pixels**, 288 KiB uncompressed, ~40.5 times fewer pixels than full GRAY8. These are not interpolated or downscaled pixels.
- Mac injects the cropped pixels into two cached full-size sensor/working images and runs the existing detector, seed learning model, 40-column robust fusion and F-Direct unchanged. The current **full-frame photo on disk is NOT updated** for each fast frame; the live panel and detector use new pixels in memory. Stop FAST and capture a new full frame if you need to save/inspect a complete photograph.
- High-rate continuous loop omits the old post-completion minimum 0.5-second idle period. Acquisition remains **sequential** (no unbounded queue), in existing HTTP worker threads with Tk processing on main thread. This release does NOT promise 5 FPS. The camera still reads a full 4608×2592 sensor frame; v14.7.1 mainly accelerates subsequent conversion/compression/transfer/disk paths.
- Web UI Heatmap v2: **Raw width relative to global center**, **Local deviation relative to frame median**, **Difference from first five visible frames**; fixed ±0.10/0.25/0.50/1.00 px or auto display range, rejected-column hatching, and a rolling effective measured FPS estimate. All read-only LAN features retained. **Not actual surface roughness**.
- Focus/session/settings epoch must match the full-frame background. Mismatch, invalid PNG, invalid geometry or unsupported Pi endpoint cause **FAST to stop automatically**, not continue using stale data. `measurements_v1471/fast_roi_failures.log` retains reasons.
- Successful fast-frame timing records: `measurements_v1471/fast_roi_YYYYMMDD_HHMMSS.csv` contains per-frame camera-side capture time, HTTP latency, full detector time, effective FPS and diameters. Browser CSV export contains the original v14.7.0 measurements and 40-column raw widths.

## 1. Save the stable version first

Do not overwrite the working `filament_live_v1470` directory. Keep a copy and **do not edit or delete** the existing `pi_live_camera_server_v145.py` on Raspberry Pi.

Extract this ZIP **beside** the existing v14.7.0 folder. Previously saved configuration, `config/dual_mirror_v1467.json`, references and captures in your existing v14.7.0 folder are NOT magically copied. You may copy your existing `config/` and `captures_live_v146/` to v14.7.1 before launching; verify all calibration paths / settings and capture a fresh full-size EMPTY background as required by your original workflow.

## 2. Raspberry Pi side (Pi SSH terminal; run these actions from Mac host)

Upload the NEW file only (replace `<PI_USER>` and `<PI_HOST>` with **your existing working SSH login**):

```bash
# MAC HOST terminal
cd ~/filament_live_v1471
scp pi_live_camera_server_v1471.py <PI_USER>@<PI_HOST>:~/pi_live_camera_server_v1471.py
ssh <PI_USER>@<PI_HOST>
```

**On the RASPBERRY PI SSH terminal**, stop the OLD server normally in its terminal (Ctrl+C), then run the new file with **the same parameters, especially host, port, camera focus, exposure, gain and FPS** as you used on the old server. Example **only**:

```bash
# RASPBERRY PI SSH terminal (example; preserve YOUR existing args)
python3 ~/pi_live_camera_server_v1471.py --host 127.0.0.1 --port 8765 --fps 8 --lens-position 9.515 --exposure-us 1000 --gain 1.0
```

Do **not** run both Pi servers on the same port. Keep your existing Mac→Pi tunnel or Pi LAN routing unchanged. Also note the read-only Mac web dashboard and Pi tunnel may each use TCP 8765 on different hosts; **two listeners cannot bind the same port on the Mac**. If necessary, set **Mac Web Dashboard port 8766** but leave the Pi camera URL/tunnel as already working.

Mac endpoint preflight (MAC HOST terminal; use the camera URL you actually see in the Tk live controls; example assumes a local SSH tunnel):

```bash
curl -sS -D /tmp/roi1471.headers -o /tmp/roi1471.png 'http://127.0.0.1:8765/snapshot_roi.png?x0=1200&width=64'
grep -i 'X-ROI-\|X-Pixel-Format' /tmp/roi1471.headers
file /tmp/roi1471.png
```

Expected: PNG, 4608×64 pixels in **sensor orientation**, pixel format GRAY8. If response is 404/409, check the Pi service and URL before running fast measurement. **Do not change the camera exposure/focus after background capture.**

## 3. Mac installation and launch (MAC HOST terminal; not Docker)

```bash
cd ~/filament_live_v1471
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1471.py
```

Or run `START_v1471_Mac.command` after setting up `.venv`.

1. Restore/verify the existing working calibrations (Left + Right) and ROI positions **without moving hardware**. Take a **new full-frame EMPTY background** and a **full-frame WITH filament** image, as in v14.7.0. Complete/verify seed and local scale if required.
2. Start the LAN Web Dashboard with the same settings as v14.7.0. Refresh browser to load Heatmap v2.
3. Only after setup, tick `Use FAST ROI for Continuous Measurement` in Mac Tk window.
4. Press the ORIGINAL `START CONTINUOUS MEASUREMENT` button. FAST should start automatically, with its effective FPS reported in the Tk fast panel and the web Overview (last 20 samples).
5. For the ONE acceptance run: keep the material static and collect around **150–200 frames** or about **30–60 seconds**, whichever comes later. Check mean, standard deviation and `Valid mm frames`. Export CSV from the browser, and preserve `measurements_v1471/fast_roi_*.csv` from Mac.
6. Press `STOP CONTINUOUS MEASUREMENT` once. Avoid making new calibration or camera control changes during the run.

## 4. Acceptance / fail-fast (at most ONE physical trial)

| Metric | Proposed threshold | What to do if failed |
|---|---|---|
| Working full-frame calibration prior to FAST | Baseline valid and stable | Don't start FAST |
| Complete fast request / native pixels | Proper 64×4608 GRAY8 PNG; unchanged epochs | Stop and use v14.7.0 |
| Sustained observed measurement throughput | **≥5 successful frames/s** for ≥30 s | Submit v14.7.0 |
| Valid calibrated fused frames | **≥95%** during static test | Submit v14.7.0 |
| Stable static diameter | Std ≤**1.2 μm** (provisional threshold vs prior 0.61 μm / 10-frame baseline) | Submit v14.7.0 |
| Crashes/lockups/errors | Zero; STOP works and saves partial logs | Submit v14.7.0 |

**Important:** The previous 0.61 μm comes from only 10 static samples. This proposed threshold is an engineering decision for an expedient test, not a statistically validated universal tolerance. Above-threshold variation may be physical or caused by different environmental conditions.

**FPS calculation:** actual detector completion timestamps, not `1/Interval`, browser refresh rate, MJPEG preview FPS or camera nominal 8 FPS.

## 5. Rollback (no troubleshooting required on deadline)

Mac: stop current test, quit v14.7.1, and open the already working `filament_live_v1470` folder:

```bash
# MAC HOST terminal
cd ~/filament_live_v1470
source .venv/bin/activate
python filament_diameter_ui_v1470.py
```

Pi: stop v14.7.1 camera server and restart the **original** `pi_live_camera_server_v145.py` with your previous arguments. Restore the already working tunnel/network routing. No calibration or original code is deleted by this new release.

## Tests (offline Mac)

```bash
# MAC HOST terminal, with virtual display only on a headless CI machine
python -m unittest -v test_v1471 test_v1470 test_v1469 test_v1468 test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

The automated suite includes byte-equivalence of source pixel strips, native 64×4608 GRAY8 PNG decoding, epoch checks, Pi snapshot mocking, Tk mode switching and previously working algorithms. It does **not** measure real FPS or promise hardware success. **No ECC, Kalman filter or changed diameter estimator**.

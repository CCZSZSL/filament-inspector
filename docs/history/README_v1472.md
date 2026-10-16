# Filament Inspector v14.7.2 — Advanced Animated Web Console

**Change scope: WEB UI ONLY.** This version is based on the tested v14.7.1 package. The original 68 packaged v14.7.1 files are included unchanged. The existing Raspberry Pi Camera Server `pi_live_camera_server_v1471.py`, FAST ROI acquisition, all calibration logic, seed learning, 40-column subpixel detector, F-Direct, experiment acquisition and CSV processing algorithms are **unchanged**. The hardware-verified measured throughput for the previous version was about **2.19 FPS**; v14.7.2 does **not** claim a faster acquisition rate or better metrology.

## New web interface

- **Dark futuristic scientific console by default**: illuminated sensor emblem, dark grid background, animated data-flow bar, glowing live traces and refined engineering cards. **LIGHT / DARK** toggle on the top bar.
- **Animations**: low-intensity status and data pulses, metric-card update flash, signal activity, page transition and lightweight graph highlights. **MOTION / STATIC** toggle stops purely visual animations, and system **prefers-reduced-motion** is respected.
- **Trend microcharts** (sparklines) in KPI cards use *actual browser-received measurements*; they are never generated from a fictitious signal.
- **Pause display / Resume display** freezes the browser's displayed snapshot only. The Mac continues capturing, measuring, logging and serving newer data. Header connection status stays current. A banner explicitly indicates that the display is paused.
- **Chart window**: most time-based charts and the heatmap show the last 40, last 120 or all available samples. Measurement summary statistics and CSV export always use **all available samples**, regardless of chart window selection.
- **Tooltips**: point at diameter, mirror-difference or Y-position trends to inspect the actual nearest sample; hover heatmap cells to inspect original pixel width, local/reference deviation and accepted/rejected status.
- **Expand chart**: click the small ⤢ control in a chart panel; click again or press Escape to return to normal view.
- **3D auto orbit**: explicit ON/OFF control (off by default). Manual mouse/touch drag and wheel zoom remain available. 3D displays only legitimate axial-feed tagged data. It is still an **assumed circular-section diameter reconstruction**, not Ra/Rz roughness or actual roundness.
- **Responsive mobile/tablet layout**: same features over an accessible same-network URL, no web fonts, frameworks, image CDNs or public internet required.
- **Read-only by design**: Web UI cannot operate the camera, alter calibration, change background frames or start/stop measurements. The Tkinter calibration UI still owns all actions.

### Scientific integrity safeguards

- **No smoothing, fabricated values, Kalman filtering or ECC registration** was added.
- Invalid / Pixel Only samples continue to display as gaps in diameter plots. Raw valid pixel widths remain accessible via heatmap and CSV.
- All fusion, tolerance, and statistics retain their definitions. Web nominal/tolerance controls affect **visualization only**, not calibration or acquisition.
- Graph expansion, glow, color, auto orbit and radial exaggeration are **visual only**.
- The Mac server is still an unauthenticated HTTP listener intended only for a **trusted local network**. Do not expose port to the internet. An HTTP page may show "Not secure" because it does not use TLS.

## Installation / run (Mac host terminal, NOT Docker)

Extract ZIP **alongside** v14.7.1, not on top of it. Keep a fully working v14.7.1 installation as your fallback. Your prior `config/`, saved setup JSON, and `captures_live_v146/` data must be copied across **if needed**; the ZIP does not automatically migrate local personal files. Verify the setup and capture a new empty background when the original workflow requires it.

```bash
cd ~/filament_live_v1472
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1472.py
```

Alternatively double-click `START_v1472_Mac.command` (after preparing `.venv`) or run that script in Mac Terminal.

**Raspberry Pi SSH terminal:** Keep running your currently working **`pi_live_camera_server_v1471.py` with the same working arguments**. **No new Pi upload or camera server update is needed.** Keep your existing SSH port forwarding, camera URL, exposure/focus/gain and LAN browser port arrangement unchanged.

1. In Tkinter, use the original workflow to load saved setup, verify ROI/seed/calibration, acquire a fresh empty background if needed, then start normal or Fast ROI continuous measurement.
2. In Tkinter, press **START WEB DASHBOARD** (or reuse the same existing LAN URL if already configured on this Mac). Browse to the displayed Mac address, such as `http://192.168.x.x:8765/` (example only).
3. Web header provides **MOTION / STATIC** and **LIGHT / DARK** appearance toggles. The chart toolbar provides **40 / 120 / All** and **Pause display**; the 3D page provides **Auto orbit**.
4. The **Export CSV** action exports stored server measurements, including Pixel Only records. It is **not** affected by the display pause or history window.
5. If you want the original interface, exit v14.7.2 and run `python filament_diameter_ui_v1471.py` in the same folder or launch your preserved original v14.7.1 folder.

## Performance and accessibility

Animations are limited to CSS compositing where practical. Auto orbit is opt-in and renders only when the 3D page is visible. The browser still polls the existing API about every 1.2 seconds; acquisition and browser rendering remain separate. For older mobile devices, choose **STATIC** to disable visual motion. Users who have "reduce motion" enabled at the OS level start with most decorative motion disabled.

The browser's sampling window controls presentation only. Web service memory retention is still capped by its server-side row limit; it does not change the measurement capture frequency.

## Tests

**Offline software tests** can be run on Mac host from this folder:

```bash
python -m unittest -q test_v1472 test_v1471 test_v1470 test_v1469 test_v1468 test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

The new suite checks all five pages, control bindings, read-only API behavior, Pixel Only retention, CSV export, assets and backwards compatibility. It is complemented by a locally rendered headless Chromium functional review of pause/resume, history selector, heatmap tooltip, auto-orbit and responsive layout using **synthetic demonstration data**, which is not included as a measurement dataset.

**v14.7.2 doesn't require any Python packages beyond `requirements.txt`; the test browser is not needed for regular use.**

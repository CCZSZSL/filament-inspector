# Filament Inspector v14.7.0 — Mac Calibration + LAN Web Dashboard

## Architecture

- **Calibration and camera operation:** the original Tkinter Mac application. It keeps the v14.6.9 interface, including camera connection, background capture, ROI setup, seed learning, dual-mirror local calibration, optional Y compensation, 40-column fusion, continuous measurement and Static/Motion tests.
- **Camera acquisition:** still performed by the Mac from the existing Raspberry Pi Camera Server, using its original full-resolution GRAY8 PNG endpoint. **Do not edit or restart the Pi server for this release.**
- **LAN dashboard:** a standard-library `ThreadingHTTPServer` on the **Mac** (not the Pi). It serves the offline HTML/CSS/JS app and periodic read-only JSON snapshots from the original detector. Every device that can reach the Mac over the same network can open the page; this does not require Python or extra software on the viewing device.
- **Isolation:** network clients cannot trigger camera captures, change settings, start/stop tests or recalibrate. Web requests never call Tk or the detector. Measurement algorithms, Pi Server and every original v14.6.9 file are unchanged.

The web server binds to `0.0.0.0` on a configurable port, initially `8765`. It starts **only after clicking START WEB DASHBOARD** in the original Tk window. You may complete/restore calibration first. It does not automatically turn on camera capture. All browsers show the same live dataset and auto-refresh about every 1.2 seconds. The web interface requires no internet/CDN.

## Installation — Mac HOST terminal (not Raspberry Pi, not Docker)

Unpack `filament_live_v1470.zip` into your chosen directory, for example `~/Downloads`:

```bash
cd ~/Downloads/filament_live_v1470
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1470.py
```

In later sessions:

```bash
cd ~/Downloads/filament_live_v1470
source .venv/bin/activate
python filament_diameter_ui_v1470.py
```

Or double-click `START_v1470_Mac.command` after the `.venv` is installed.

**Important:** If you unpack the application into a different folder, the prior installed calibration JSON and reference images are not automatically moved. Restore the previous setup from Tk if needed, and verify focus/background/reference conditions before measuring. Do not infer that restoring a JSON automatically revalidates the physical geometry.

## Using LAN web visualization

1. Mac and viewing device must share a **reachable** trusted network (home Wi-Fi, trusted hotspot or Ethernet bridge). The Pi and the Mac can also use this network, but browser requests go only to the **Mac**.
2. Start `filament_diameter_ui_v1470.py` on the Mac. Configure the original camera, empty background, ROIs, seed/scale and check calibration via the Tk GUI.
3. In the new **LAN Web Dashboard** panel, leave `Port=8765` or choose another free TCP port. Click **START WEB DASHBOARD**.
4. On the Mac, click **OPEN IN MAC BROWSER**. On another device, open the `http://<Mac-LAN-IP>:8765/` address displayed in the Tk panel. **COPY LAN URL** copies a suggested address; if several interfaces are listed, choose the IP for the network shared by both devices.
5. Start **Continuous Measurement** or **Static / Motion Experiment** from the original Mac Tk GUI. The browser displays newly processed measurements; it does *not* itself acquire frames.
6. Click **STOP WEB** to close remote access. Measurement and the Tk app continue working. Closing the Tk app also stops the web listener.

**Find the Mac IP manually — Mac HOST terminal:**

```bash
ipconfig getifaddr en0
# If empty, try another connected interface such as en1:
ipconfig getifaddr en1
```

Example: if the Mac's LAN IP is `192.168.1.48`, open **http://192.168.1.48:8765/** on the phone/tablet. `127.0.0.1` or `localhost` only works in a browser on the Mac itself. A phone connecting to the *same SSID* is not always enough when the router has **AP / client isolation** enabled. If access fails, allow incoming Python connections in the macOS Firewall, confirm host IP/port, and test with the Mac and phone on the same subnet. VPN or an isolated school/guest Wi-Fi may block device-to-device connections.

**Security:** no authentication or encryption is provided. Anyone who can reach the Mac on this port can **read** the current history and download the recent dashboard CSV (but cannot alter the camera or calibration). Use only trusted networks; **do not** expose/forward TCP port 8765 to the public internet. The listener is manually opt-in and can be stopped from Tk at any time.

## Five browser pages

| Page | Visualization and scope |
|---|---|
| Overview | Current virtual F-Direct diameter, mean, sample std, latest ΔLR, valid frames, adjustable *visual-only* tolerance; live lines, diameter histogram and signed difference. |
| Diameter Analysis | Left, Right, F-Direct vs elapsed time; nominal ±tolerance lines, histogram, adjacent-sample moving range; unfiltered descriptive statistics. |
| Mirror & Position | Left-vs-Right scatter; Bland–Altman (difference vs mean), position tracking and Y-vs-diameter scatter (views individually color coded), calibration status and current 40-column raw pixel-width heatmap. |
| 3D Profile | Mouse/touch drag to rotate; wheel to zoom, visual-only radial exaggeration; **approximate axisymmetric** model for explicitly enabled real axial-feed continuous runs. |
| Experiments | Local Mac test mode, attempts/progress and last measured rows, including Pixel Only and explicit HTTP-capture failures. It does not start tests remotely. |

Export CSV from any browser using **Export CSV**. The web export contains the **most recent 1500 dashboard samples**, including Pixel Only, failure reasons, available widths, and up to 40 **raw** edge widths per mirror; it is not a substitute for the complete original Mac-side experiment/continuous logs. Historical experiments can be imported with the original Mac Dashboard **Load CSV** while not acquiring; position fields and Pixel Only statuses are preserved, but no unknown axial positions are fabricated.

### Metrology caveats

- `F-Direct = (Left + Right)/2` is **virtual**, not a third independent camera view.
- `ΔLR = |Left − Right|` is an **inter-view agreement diagnostic**, not ovality/roundness.
- The browser 3D model assumes circular cross sections. It does **not** measure actual scratches, height maps, surface roughness Ra/Rz, or true roundness; the radius may be amplified for visualization without changing the measured values.
- Model `z` uses elapsed *Mac measurement completion time* multiplied by a user-supplied nominal axial-feed speed. Camera exposure timestamps, feed slip and encoder synchronization are **not** accounted for. Missing-mm sections and separate sessions are not joined. Static and lateral motion tests have no axial 3D profile.
- The heatmap shows original 40-column silhouette-edge **widths in pixels**, not microscopic surface texture. Fusion-rejected values can still appear as raw observations; pixel-width data must not be treated as valid mm when outside the local calibration range.
- A 0.71 µm sample standard deviation from a static test is **repeatability**, not absolute diameter accuracy. Experimental calibration validity and sampling pitch still require physical validation.
- The GRAY8 Pi Camera Server remains unchanged: **no RGB color measurement** is implemented in this release. No Kalman filter or ECC registration is introduced.
- Adjustable tolerance is stored only within the current browser interface, and does not alter calibrations, detector decisions, original CSV, or measurement history. These are *specification* limits, **not** statistical control limits.

## Tests — Mac HOST terminal

```bash
python -m unittest -v test_v1470 test_v1469 test_v1468 test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

The offline suite verifies LAN HTTP pages, JSON, CSV, read-only methods, sanitization, concurrent publication, start/stop/rebind, Tk integration, existing controls, 40-column output and previous measurement regressions. Automated testing cannot validate actual Pi/network hardware or macOS firewall behavior: perform final tests with a real smartphone and the IMX708.

## Recovery and backwards compatibility

- Mac Tk v14.6.9 remains directly launchable: `python filament_diameter_ui_v1469.py`.
- Stable v14.6.8 / v14.6.7 versions remain in the same folder and can be launched similarly.
- All original v14.6.9 project files including Pi Server are copied byte-for-byte. Web visualization is isolated in `filament_web_v1470.py`, `web_v1470/`, and `filament_diameter_ui_v1470.py`.

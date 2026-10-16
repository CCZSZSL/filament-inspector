# Current setup

Use `filament_diameter_ui_v1472.py` on the host and `pi_live_camera_server_v1471.py` on the Raspberry Pi. Run commands from the repository root. The numbered notes in `history/` describe earlier packages and can refer to files no longer present in the current tree.

## Camera service

Use Raspberry Pi OS with a supported camera, Picamera2/libcamera, OpenCV and NumPy. Inspect available arguments:

```bash
python3 pi_live_camera_server_v1471.py --help
```

For an already working rig, retain its established camera arguments and calibration geometry. The following is only an example, not a calibration recommendation:

```bash
python3 pi_live_camera_server_v1471.py --host 127.0.0.1 --port 8765 --fps 8 --lens-position 9.515 --exposure-us 10000 --gain 1.0
```

The same service provides `/snapshot.png`, `/stream.mjpg`, `/status` and the optional `/snapshot_roi.png` route. Full-frame measurement does not require the removed v14.5 server.

From a separate host terminal, forward the loopback camera service using your own SSH account and address:

```bash
ssh -N -L 8765:127.0.0.1:8765 <PI_USER>@<PI_HOST>
```

Keep that terminal running. The host camera URL is then `http://127.0.0.1:8765`. Do not run competing services on the same port. When this tunnel occupies port 8765 on the host, select another host dashboard port, such as 8766.

## Host workflow

1. Install the host dependencies and launch v14.7.2 as described in the main README.
2. Connect the camera and confirm the actual focus, exposure, gain and image geometry.
3. Acquire a fresh empty background and a full-frame image with the specimen. Configure the measurement region and seed/local calibration using an appropriate measured reference.
4. Begin full-frame measurements and check validity before optionally enabling Fast ROI for continuous measurement.
5. Start the read-only dashboard in Tkinter. Open the displayed host LAN address from trusted devices.
6. Preserve host-side logs for complete experiments; browser export is limited to the published history.

Changing camera settings or the optical geometry can invalidate the background or calibration. A previous user's calibration is not a substitute for calibrating your own rig.

## Fast ROI

Fast ROI is off by default. It transfers an original-pixel strip rather than a rescaled image and requires the expected 4608 × 2592 source geometry, matching camera epochs and valid measurement setup. Background, calibration and experiment operations continue to use full frames. A failed or stale ROI request stops Fast ROI rather than silently accepting stale data.

Use **STOP FAST / USE STABLE FULL FRAME** to return to the current application's full-frame path. Actual throughput must be measured on your hardware; the camera's requested FPS and browser refresh interval are not completed measurement rates.

## Older releases

The full original public package remains recoverable at commit [`2005c15`](https://github.com/CCZSZSL/filament-inspector/tree/2005c151068def0fabea945bc5f7ea99d9832dda). Recover it into a separate checkout if you specifically need an older launcher, UI or camera service. This repository cleanup does not change an existing Mac or Pi installation.

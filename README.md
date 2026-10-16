# Filament Inspector

Camera-based filament diameter inspection using a Raspberry Pi camera server, a Python/Tkinter host application, and a read-only browser dashboard. This repository publishes the v14.7.2 project package.

## Authors

- [CCZSZSL](https://github.com/CCZSZSL)
- [lyin1252-stack](https://github.com/lyin1252-stack) — co-author

## Features

- Full-resolution grayscale acquisition and optional native-pixel Fast ROI transport.
- Seed-guided edge detection, local per-view calibration, position-validity checks and F-Direct fusion of two reflected views.
- Experiment workflows and diagnostic CSV records, including invalid and pixel-only measurements.
- Overview, Diameter Analysis, Mirror & Position, 3D Profile and Experiments pages.
- Read-only browser monitoring; acquisition and calibration remain controlled by the host application.

## Host installation

The host workflow targets macOS. Install Python 3 with Tkinter (check with `python3 -m tkinter`), then:

```bash
git clone https://github.com/CCZSZSL/filament-inspector.git
cd filament-inspector
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python filament_diameter_ui_v1472.py
```

Alternatively run `zsh START_v1472_Mac.command` after preparing the environment. No personal calibration, camera images or measurement logs are included. Configure your camera connection, acquire an empty background, set the ROI, and calibrate for your geometry before measuring.

## Raspberry Pi camera service

Use Raspberry Pi OS with a supported camera and Picamera2/libcamera installed through the operating system. The Pi also needs OpenCV and NumPy. The host `requirements.txt` does not install the Pi camera stack.

```bash
python3 pi_live_camera_server_v1471.py --help
```

See [the camera and Fast ROI instructions](README_v1471.md) for camera parameters and SSH forwarding. The older `pi_live_camera_server_v145.py` is retained as a full-frame alternative. Run only one camera server at a time. Verify focus, exposure, gain and geometry before acquiring a background. Avoid port conflicts between the camera tunnel and host dashboard.

## Dashboard and data

Press **START WEB DASHBOARD** in Tkinter and open the host address shown there. Display pause, chart history and tolerance controls do not modify acquisition or calibration. CSV export retains available diagnostics for invalid and pixel-only rows. The dashboard exposes up to the latest 1,500 published records; preserve complete host-side experiment logs separately.

The HTTP services do not provide authentication or TLS. Use a trusted network or appropriate SSH forwarding and do not expose their ports directly to the public internet.

## Measurement limitations

This is a research/coursework prototype, not a certified measuring instrument. Repeatability, agreement between views and deviation from nominal diameter do not independently establish absolute accuracy. Calibration validity depends on optical geometry and operating conditions. The 3D display assumes circular cross-sections and estimates axial distance from feed speed and elapsed time; it does not measure full roundness or surface roughness.

v14.7.2 changes the web interface while retaining the v14.7.1 measurement backend. Historical release notes include earlier experimental observations and provisional acceptance criteria; these are not new validation results for this public release. See [v14.7.2 notes](README_v1472.md).

## Offline tests

With dependencies installed, run from the repository root:

```bash
python -X utf8 -m unittest -q test_v1472 test_v1471 test_v1470 test_v1469 test_v1468 test_v1467 test_v1466 test_f_direct_v1463 test_dynamic_fusion_v1463 test_seed_guided_v1462 test_v142 test_v1421_boundary_guard
```

Some tests create Tk windows and require a graphical desktop (or a virtual display on Linux). Camera behavior is mocked; passing offline tests does not verify optical accuracy, real throughput, or physical hardware compatibility.

The `-X utf8` option avoids decoding failures on Windows installations whose default encoding is GBK.

## Layout

- `filament_diameter_ui_v1472.py`: current host entry point.
- `filament_*.py`: acquisition, calibration, measurement and interface modules.
- `pi_live_camera_server_v1471.py`: optional Fast ROI camera service.
- `web_v1472/`: current dashboard assets.
- `test_*.py`: offline regression tests.
- `README_v*.md`: historical release and operating notes.

Older versioned modules and assets are retained because the current application inherits from them. Do not remove them solely because their names refer to an earlier version.

## License

Project code is released under the [MIT License](LICENSE). Dependencies retain their own licenses.

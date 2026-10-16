# Filament Inspector

Camera-based filament diameter inspection using a Raspberry Pi camera server, a Python/Tkinter host application, and a read-only browser dashboard. This repository publishes the v14.7.2 project package.

This branch reorganizes v14.7.2 into a functional Python package. Measurement formulas and saved calibration formats are preserved. See [the architecture and migration guide](docs/ARCHITECTURE.md).

## Authors
- [lyin1252-stack](https://github.com/lyin1252-stack)
- [CCZSZSL](https://github.com/CCZSZSL)


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
git switch refactor/modular-framework
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m filament_inspector
```

Alternatively run `zsh START_v1472_Mac.command` after preparing the environment. No personal calibration, camera images or measurement logs are included. Configure your camera connection, acquire an empty background, set the ROI, and calibrate for your geometry before measuring.

## Raspberry Pi camera service

Use Raspberry Pi OS with a supported camera and Picamera2/libcamera installed through the operating system. The Pi also needs OpenCV and NumPy. The host `requirements.txt` does not install the Pi camera stack.

```bash
python3 -m filament_inspector.camera --help
```

See [the current setup guide](docs/SETUP.md) for camera parameters and SSH forwarding. The camera module supports both full-frame and Fast ROI acquisition. Copy the complete package to the Pi; the old filename is now only a compatibility launcher and cannot be deployed alone. Run only one camera server at a time. Verify focus, exposure, gain and geometry before acquiring a background. Avoid port conflicts between the camera tunnel and host dashboard.

## Dashboard and data

Press **START WEB DASHBOARD** in Tkinter and open the host address shown there. Display pause, chart history and tolerance controls do not modify acquisition or calibration. CSV export retains available diagnostics for invalid and pixel-only rows. The dashboard exposes up to the latest 1,500 published records; preserve complete host-side experiment logs separately.

The HTTP services do not provide authentication or TLS. Use a trusted network or appropriate SSH forwarding and do not expose their ports directly to the public internet.

## Measurement limitations

This is a research/coursework prototype, not a certified measuring instrument. Repeatability, agreement between views and deviation from nominal diameter do not independently establish absolute accuracy. Calibration validity depends on optical geometry and operating conditions. The 3D display assumes circular cross-sections and estimates axial distance from feed speed and elapsed time; it does not measure full roundness or surface roughness.

v14.7.2 changes the web interface while retaining the v14.7.1 measurement behavior. Historical release notes include earlier experimental observations and provisional acceptance criteria; these are not new validation results for this public release. See [the historical notes](docs/history/README.md).

## Offline tests

With dependencies installed, run from the repository root:

```bash
python -X utf8 -m unittest discover -s tests -p "test_*.py" -q
```

Some tests create Tk windows and require a graphical desktop (or a virtual display on Linux). Camera behavior is mocked; passing offline tests does not verify optical accuracy, real throughput, or physical hardware compatibility.

The `-X utf8` option avoids decoding failures on Windows installations whose default encoding is GBK.

With the original Git history available, compare deterministic measurements against the pre-refactor baseline:

```bash
python -X utf8 tools/compare_baseline.py
```

The baseline comparison uses synthetic images and checks numerical results, validity and rejection behavior; it is not an optical accuracy test.

## Layout

- `filament_inspector/algorithms/`: edge extraction, ROI localization, seed tracking and fusion; independent of Tk.
- `filament_inspector/calibration/`: scale calibration and position compensation.
- `filament_inspector/acquisition/`: native-pixel ROI transport validation.
- `filament_inspector/recording/`: experiment records and axial profiles.
- `filament_inspector/ui/`: Tk screens, controls and orchestration, named by responsibility.
- `filament_inspector/web/`: one read-only HTTP service and one set of dashboard assets.
- `filament_inspector/camera.py`: Raspberry Pi camera service.
- `filament_inspector/paths.py`: common configuration/capture/log root.
- `tests/`: regression tests and package-boundary checks.
- `tools/compare_baseline.py`: repeatable old/new measurement comparison.
- `docs/SETUP.md`: current camera/host setup.
- `docs/history/`: historical release notes, not current launch instructions.
- `docs/ARCHITECTURE.md`: current architecture, entry points, migration and remaining constraints.
- `docs/CODEBASE.md`: audit of the earlier cleanup before this refactor.

The old host and camera executable filenames still work as small compatibility launchers. Internal versioned imports have been replaced by package imports. By default, configuration, captures and logs remain at the repository root, as before. Set `FILAMENT_INSPECTOR_DATA_DIR` before launching to select another writable data root; no files are automatically migrated.

## License

Project code is released under the [MIT License](LICENSE). Dependencies retain their own licenses.

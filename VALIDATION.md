# Validation record

## Modular framework branch — 16 October 2026

- Branch: `refactor/modular-framework`; baseline `d7a953decb0363b63acaccee0b1978af9b328a44`.
- All 96 tests passed with exit code 0 and no skips (28.210 seconds): the existing 92 regression cases plus four package-boundary/path/entry-point checks.
- `python -X utf8 tools/compare_baseline.py` reported exact agreement for 43 deterministic cases, including numerical results, validity and error outcomes.
- AST comparison confirmed that eight extracted helper functions, two engine methods and the background locator retain their original syntax trees.
- Headless checks block imports of Tk, UI modules and Pi camera libraries while loading algorithm/calibration modules and exercising an intensity crossing.
- Data-root tests verify that saved calibration and experiment output paths follow the selected writable root. Default paths remain checkout-relative; static web assets remain package-relative.
- Historical GUI teardown diagnostics remain present. These tests do not establish live camera, physical accuracy, macOS deployment or throughput performance.
- Runtime module locations/imports, UI class names and entry points were reorganized. The duplicate base web server/assets were consolidated into the current dashboard service. See [architecture](docs/ARCHITECTURE.md) for migration details and retained inheritance constraints.

## Repository cleanup — 16 October 2026

The following is the earlier cleanup result on `main`, before modularization.

- All 92 tests passed using `python -X utf8 -m unittest discover -s tests -p "test_*.py" -q` (25.473 seconds, exit code 0, no skipped tests).
- Fast ROI UI tests now instantiate the retained v14.7.2 class. Existing pixel, epoch, stop/fallback and mocked camera tests were preserved.
- All 38 remaining root Python files are reachable in the local import graph from the host and camera entry points. Dynamic/file-relative references were also reviewed.
- Retained runtime Python files and v14.7.0/v14.7.2 web assets match the original public commit, allowing for Git newline normalization. No measurement algorithm was edited.
- Current and historical Markdown links were checked; no broken local links were found.
- Tk callback/theme cleanup diagnostics still occur during GUI test teardown, as in the initial publication run.
- No new optical, live camera or macOS hardware validation was performed. No running Mac/Pi deployment was changed.

The [codebase audit](docs/CODEBASE.md) documents all ten removed files and recovery from Git history.

## Initial publication

Date: 16 October 2026.

- Source: the supplied v14.7.2 package (76 files). Runtime source, tests, assets and launchers were copied without content changes. Only example workstation paths in historical Markdown notes were generalized.
- Environment: Windows, CPython 3.12.13, NumPy 2.5.4, OpenCV Python 5.0.0.93, Pillow 12.3.0.
- Result: all 92 tests in the README command passed in UTF-8 mode.
- The default Windows GBK mode initially produced two file-decoding errors. The documented `-X utf8` invocation resolved them without source changes.
- Tk emitted callback/theme cleanup diagnostics while test windows were destroyed; the UTF-8 test run nevertheless completed with exit code 0 and `OK`.
- No live camera, optical accuracy, acquisition throughput or macOS hardware validation was performed for this publication.
- The public package excludes personal calibration settings, experiment logs, images and local virtual environments. A common credential-pattern scan of the staged files found no matches; this is not a full security audit.

Historical performance claims in versioned release notes remain historical observations, not measurements made during publication validation.

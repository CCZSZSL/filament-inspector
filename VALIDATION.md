# Publication validation

Date: 16 October 2026.

- Source: the supplied v14.7.2 package (76 files). Runtime source, tests, assets and launchers were copied without content changes. Only example workstation paths in historical Markdown notes were generalized.
- Environment: Windows, CPython 3.12.13, NumPy 2.5.4, OpenCV Python 5.0.0.93, Pillow 12.3.0.
- Result: all 92 tests in the README command passed in UTF-8 mode.
- The default Windows GBK mode initially produced two file-decoding errors. The documented `-X utf8` invocation resolved them without source changes.
- Tk emitted callback/theme cleanup diagnostics while test windows were destroyed; the UTF-8 test run nevertheless completed with exit code 0 and `OK`.
- No live camera, optical accuracy, acquisition throughput or macOS hardware validation was performed for this publication.
- The public package excludes personal calibration settings, experiment logs, images and local virtual environments. A common credential-pattern scan of the staged files found no matches; this is not a full security audit.

Historical performance claims in versioned release notes remain historical observations, not measurements made during publication validation.

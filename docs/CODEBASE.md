# Codebase cleanup audit

**Historical snapshot:** this audit describes the first cleanup on `main`, before the modular-framework branch. The file names and 38-file layout below are historical. For the current branch, see [architecture](ARCHITECTURE.md).

Reviewed on 16 October 2026 against the original public commit [`2005c15`](https://github.com/CCZSZSL/filament-inspector/tree/2005c151068def0fabea945bc5f7ea99d9832dda).

## Retention rule

The local Python import graph was traversed from `filament_diameter_ui_v1472.py` and `pi_live_camera_server_v1471.py`. Their transitive closure contains **38 Python files**. Dynamic imports, file-relative paths, test imports, static asset loading and the differences between camera-server versions were also inspected. The audit establishes file-level dependencies; it does not claim that every function inside every retained file is reachable.

Most older UI modules are active base classes. Deleting them by version number breaks the current application. Their locations are also used for saved settings, capture directories and web assets, so this cleanup leaves runtime modules in place. Refactoring that inheritance chain into a smaller package would be a separate behavioral change requiring additional validation.

## What remains useful

| Group | Why retained |
|---|---|
| Current host entry and `START_v1472_Mac.command` | One documented launch route for v14.7.2. |
| Earlier UI classes through v14.7.0 | Current UI inherits their camera, ROI, calibration, processing and experiment behavior. |
| Edge, seed, local-scale, compensation and fusion modules | Active measurement dependencies. |
| `filament_live_panel_v143.py` and `filament_live_integrated_v145.py` | Both remain in the host import/inheritance chain despite their old version labels. |
| `filament_fast_roi_v1471.py` | Current v14.7.2 still uses its native-pixel strip validation and installation logic. |
| `pi_live_camera_server_v1471.py` | Provides both original full-frame acquisition and the added Fast ROI endpoint. |
| `filament_web_v1472.py` and `web_v1472/` | Current dashboard service and assets. |
| `filament_web_v1470.py` and `web_v1470/` | The inherited v14.7.0 UI imports the service; regression tests exercise the base dashboard and its static assets. Not unused copies. |
| All 12 test modules, now in `tests/` | Preserve regression coverage for the retained measurement pipeline. Fast ROI UI tests now target v14.7.2 directly. |
| Seven release notes, now in `docs/history/` | Useful provenance, but separated from current installation instructions to avoid obsolete commands. |
| License, authors, requirements, config README and validation record | Licensing, attribution, installation, local-configuration guidance and validation provenance. |

## Removed from the current tree

“Removed” means unnecessary for the current supported entry points, not that the code never had value. All ten files remain available in Git history.

| Removed file | Evidence and reason |
|---|---|
| `START_v1468_Mac.command` | Only launches the old v14.6.8 UI; unused by the current launcher and tests. The underlying UI module remains required. |
| `START_v1469_Mac.command` | Only launches the old v14.6.9 UI; redundant as a current startup choice. |
| `START_v1470_Mac.command` | Only launches the old v14.7.0 UI; the current application already offers a full-frame mode. |
| `START_v1471_Mac.command` | Only launches the standalone v14.7.1 UI removed below. |
| `filament_diameter_ui_v1471.py` | v14.7.2 inherits directly from v14.7.0, not this file. A source diff shows the same Fast ROI implementation with version/class/dashboard substitutions. Tests were redirected to the current class before removal. |
| `filament_web_v1471.py` | Only used by the removed standalone v14.7.1 UI and its old test import. Current UI imports v14.7.2's web service. |
| `web_v1471/index.html` | Asset of the removed web service; not served by v14.7.2. Heatmap-control coverage now checks current assets. |
| `web_v1471/dashboard.css` | Styles for the removed standalone dashboard. |
| `web_v1471/dashboard.js` | Client code for the removed standalone dashboard. Current client includes the retained heatmap controls. |
| `pi_live_camera_server_v145.py` | No current local imports or tests require it. Source comparison shows v14.7.1 adds ROI support while retaining the original full-frame, status and control paths; a second camera-service copy is unnecessary for current setup. |

## Recovery and validation

Browse the original commit linked above to retrieve any removed file, or check out that commit into a separate worktree. No history was rewritten and no original local software package or running hardware deployment was modified.

Run all regression tests from the repository root:

```bash
python -X utf8 -m unittest discover -s tests -p "test_*.py" -q
```

The directory move adjusts file-relative test paths. It does not change the retained runtime algorithms. See [validation](../VALIDATION.md) for observed test results and limitations.

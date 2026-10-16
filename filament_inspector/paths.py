"""Shared writable-data location, independent of runtime module placement."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Retain the original checkout-relative config/capture/log locations by default.
# Set this before starting Python to use a separate data directory.
DATA_ROOT = Path(os.environ.get('FILAMENT_INSPECTOR_DATA_DIR', str(PROJECT_ROOT))).expanduser().resolve()

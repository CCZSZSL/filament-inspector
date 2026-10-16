#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -f .venv/bin/activate ]; then
  echo 'Create the Python virtual environment using README_v1470.md first.'
  read -r -p 'Press Enter to close...'
  exit 1
fi
source .venv/bin/activate
python filament_diameter_ui_v1470.py

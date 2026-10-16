#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -f .venv/bin/activate ]; then
  echo 'No .venv detected. Please create it with the README commands.'
  read -r -p 'Press Enter to close...'
  exit 1
fi
source .venv/bin/activate
python filament_diameter_ui_v1469.py

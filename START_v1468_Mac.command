#!/bin/zsh
# Mac host Terminal only: launch from an already prepared virtual environment.
cd "${0:A:h}" || exit 1
if [[ ! -f .venv/bin/activate ]]; then
  echo 'Create .venv first; see README_v1468.md (Mac host terminal).'
  read '?Press Return to close...'
  exit 1
fi
source .venv/bin/activate
python filament_diameter_ui_v1468.py

#!/bin/zsh
# Mac host; original measurement backend and Pi server are unchanged.
cd "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo 'Environment missing. Run: python3 -m venv .venv && source .venv/bin/activate && python -m pip install -r requirements.txt'
  read -k 1 '?Press any key to close...'
  exit 1
fi
exec .venv/bin/python filament_diameter_ui_v1472.py

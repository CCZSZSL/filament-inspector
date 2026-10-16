#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -f .venv/bin/activate ]; then
 echo 'Install v14.7.1 with README_v1471.md first.'
 read -r -p 'Press Enter to close...'
 exit 1
fi
source .venv/bin/activate
python filament_diameter_ui_v1471.py

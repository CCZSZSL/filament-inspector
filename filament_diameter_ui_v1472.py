"""Compatibility launcher; prefer python -m filament_inspector."""
from filament_inspector.ui.app import FilamentInspectorApp

FilamentUIV1472 = FilamentInspectorApp

if __name__ == "__main__":
    FilamentInspectorApp().mainloop()

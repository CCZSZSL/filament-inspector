"""Start the host application without exposing versioned internal modules."""
import argparse


def main():
    parser = argparse.ArgumentParser(description='Filament Inspector host application')
    parser.add_argument('--version', action='version', version='Filament Inspector 14.7.2')
    parser.parse_args()
    # Keep help/version and algorithm imports independent of Tk/Pi dependencies.
    from .ui.app import FilamentInspectorApp
    FilamentInspectorApp().mainloop()


if __name__ == '__main__':
    main()

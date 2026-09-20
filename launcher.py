"""PyInstaller entry point for the packaged square-drive executable."""
import multiprocessing

from square_drive_connector.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

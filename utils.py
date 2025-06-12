# utils.py
import os
import platform
import shutil
import subprocess
import sys


def get_cache_dir():
    """Return a folder for caching song data, depending on the OS."""
    system = platform.system()
    if system == "Windows":
        base_dir = os.environ.get('APPDATA', os.path.expanduser("~"))
        cache_dir = os.path.join(base_dir, "SongRemasteringCache")
    elif system == "Darwin": #Mac
        cache_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SongRemasteringCache")
    else:  # Linux and others.
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "SongRemasteringCache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def cache_file(file_path: str) -> str:
    """Copy the file into the OS-specific cache folder (if not already there) and return the cached path."""
    cache_dir = get_cache_dir()
    base_name = os.path.basename(file_path)
    cached_file = os.path.join(cache_dir, base_name)
    # Copy if file is not already cached.
    if not os.path.exists(cached_file):
        shutil.copy2(file_path, cached_file)
    return cached_file



def check_demucs_installed():
    try:
        # Try the simplest import
        import demucs  # noqa: F401
    except ImportError:
        # Lazy-load Qt so we only pull in GUI if needed
        from PyQt6.QtWidgets import QApplication, QMessageBox

        # We need a QApplication to show QMessageBox
        app = QApplication(sys.argv)

        reply = QMessageBox.question(
            None,
            "Missing Dependency",
            "Demucs is not installed.\n\nWould you like to install it now?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            # Some PyInstaller builds might not include pip, so bootstrap it
            try:
                import ensurepip
                ensurepip.bootstrap()
            except Exception:
                pass

            # Install Demucs (using --user to avoid system permission issues)
            try:
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install", "--user", "demucs"
                ])
            except subprocess.CalledProcessError as e:
                QMessageBox.critical(
                    None,
                    "Installation Failed",
                    f"Could not install Demucs:\n{e}"
                )
                sys.exit(1)

            QMessageBox.information(
                None,
                "Installed",
                "Demucs has been installed successfully.\n\n"
                "Please restart the application to continue."
            )
        # Either they declined, or we just told them to restart
        sys.exit(0)
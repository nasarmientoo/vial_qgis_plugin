# =============================
# File: ensure_dependency.py (py39-safe)
# =============================
import sys
import subprocess
import importlib
import site
import os


def _default_report(msg: str) -> None:
    # Fallback printer if no QGIS message callback supplied
    try:
        print(msg)
    except Exception:
        pass


def _ensure_user_site_on_sys_path(report) -> None:
    """
    Make sure user site-packages is importable within QGIS' Python.
    """
    try:
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path:
            sys.path.append(user_site)
            report(f"Added user site-packages to sys.path: {user_site}")
    except Exception as e:
        report(f"Warning: could not ensure user site-packages on sys.path: {e}")


def _ensure_pip(report) -> bool:
    """
    Ensure 'pip' is available for the current interpreter.
    """
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        pass

    # Try to bootstrap pip via ensurepip (bundled with Python)
    try:
        report("Pip not detected. Bootstrapping pip (ensurepip)…")
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--default-pip"])
        return True
    except Exception as e:
        report(f"Failed to bootstrap pip: {e}")
        return False


def ensure_docker_sdk(message_callback=None) -> bool:
    """
    Ensures the Python 'docker' SDK is importable in the QGIS Python env.
    Tries a user-site install if missing. Returns True on success, False otherwise.

    Usage:
        if not ensure_docker_sdk(self._msg):
            # show message and abort
    """
    report = message_callback or _default_report

    # 1) Try import first
    try:
        import docker  # noqa: F401
        return True
    except Exception:
        pass

    # 2) Ensure pip exists
    if not _ensure_pip(report):
        report("Cannot proceed without pip. Please install 'docker' manually in QGIS Python.")
        return False

    # 3) Make sure user site is on sys.path (before and after install)
    _ensure_user_site_on_sys_path(report)

    # 4) Install docker SDK to user site
    try:
        # docker 6.1+ works well on py39; 7.x is also fine. Use >=6.1.0 to be safe.
        report("Python 'docker' SDK not found. Installing to user site…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "docker>=6.1.0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        report(f"Failed to install 'docker' SDK automatically: {e}")
        report("Manual install hint (Python Console):")
        report(">>> import sys; print(sys.executable)")
        report("Then run in a shell:")
        report("> <that_python_exe> -m pip install --user docker")
        return False

    # 5) Ensure the newly-installed site is importable and import again
    _ensure_user_site_on_sys_path(report)
    importlib.invalidate_caches()

    try:
        import docker  # noqa: F401
        report("'docker' SDK installed and importable ✓")
        return True
    except Exception as e:
        report(f"'docker' package seems installed but cannot be imported: {e}")
        report("Tip: restart QGIS so it picks up the updated user site-packages.")
        return False

"""Imported before numpy / OpenCV / Pillow by every script that needs them: a missing package stops with a
message naming the interpreter, instead of a traceback (the usual cause is a python3 other than the venv's).
-h / --help still prints the script's description and usage in that case."""
import importlib
import os
import sys

REQUIREMENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")


def _hint(mod, pkg, err):
    return (f"{sys.executable} cannot import {mod} (pip package {pkg}): {err}\n"
            "These scripts need numpy, Pillow and opencv-python in the python3 that runs them. Run them with the "
            "interpreter you installed requirements.txt into (README: Install), or create a virtual environment:\n"
            f"  python3 -m venv ~/.venvs/footage-to-film\n"
            f"  ~/.venvs/footage-to-film/bin/pip install -r '{REQUIREMENTS}'\n"
            "and run the scripts as ~/.venvs/footage-to-film/bin/python <script> ...")


if __name__ == "__main__":
    print("deps.py is a library module used by build_film.py, not a command: run build_film.py --help")
    sys.exit(2)

for _mod, _pkg in (("numpy", "numpy"), ("PIL", "Pillow"), ("cv2", "opencv-python")):
    try:
        importlib.import_module(_mod)
    except ImportError as _e:
        if {"-h", "--help"} & set(sys.argv[1:]):
            _doc = (getattr(sys.modules.get("__main__"), "__doc__", None) or "").strip()
            print(f"{_doc}\n\n(the full option list needs the packages below)\n{_hint(_mod, _pkg, _e)}")
            sys.exit(0)
        sys.exit(_hint(_mod, _pkg, _e))

"""Keep the app's writable state beside the executable or source tree."""
import os
import sys
from pathlib import Path
ROOT = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
        else Path(__file__).resolve().parents[1])
for name, relative in {
    'TEMP': 'work/temp', 'TMP': 'work/temp',
    'PYTHONPYCACHEPREFIX': 'cache/pycache', 'MPLCONFIGDIR': 'cache/matplotlib',
    'XDG_CACHE_HOME': 'cache', 'PIP_CACHE_DIR': 'cache/pip',
}.items():
    directory = ROOT / relative
    directory.mkdir(parents=True, exist_ok=True)
    os.environ[name] = str(directory)
DATA_ROOT = ROOT / 'data'

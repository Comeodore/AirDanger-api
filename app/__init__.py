import sys
from pathlib import Path

_VENDOR = str(Path(__file__).resolve().parents[1] / "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

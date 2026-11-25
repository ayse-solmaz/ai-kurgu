import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("KURGU_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

"""Run the portable all-curve correctness controls from a source checkout."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
sys.path.insert(0, str(ROOT / "src"))

from generic.check import main


if __name__ == "__main__":
    main()

import glob, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from compare import norm, metrics  # noqa

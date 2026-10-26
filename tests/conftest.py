import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
upstream = os.environ.get('INGETRAZO_SOURCE')
if upstream:
    sys.path.insert(0, upstream)

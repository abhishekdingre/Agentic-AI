"""Ensures the project root is on sys.path so `from backend import ...`
resolves regardless of how pytest determines its rootdir/import mode."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

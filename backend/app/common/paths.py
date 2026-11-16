"""Folder paths shared by the backend. Everything is relative to the backend folder."""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
CACHE_DIR = BACKEND_DIR / ".cache"  # model files and cached vectors (git-ignored)

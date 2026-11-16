"""Read backend/.env into the environment (tiny, no dependency). Variables already set win.

Never print or log what this reads: .env holds our keys.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from app.common.paths import BACKEND_DIR


def load_dotenv(path: Optional[Path] = None) -> None:
    path = path or BACKEND_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))

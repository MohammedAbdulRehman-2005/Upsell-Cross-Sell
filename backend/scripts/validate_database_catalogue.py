"""Wrapper script forwarding to `app.scripts.validate_database_catalogue`."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.scripts.validate_database_catalogue import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

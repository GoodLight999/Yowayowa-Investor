from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from yowayowa.api.app import app
from yowayowa.vercel_observability import install_vercel_observability

install_vercel_observability(app)

__all__ = ["app"]

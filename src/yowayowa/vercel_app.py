from __future__ import annotations

from yowayowa.api.app import app
from yowayowa.vercel_observability import install_vercel_observability

install_vercel_observability(app)

__all__ = ["app"]

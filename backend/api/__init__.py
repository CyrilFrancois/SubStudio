# api — The UI contract (ux-review §3), mounted in main.py with one include_router. Exports: router.

from .routes import router

__all__ = ["router"]

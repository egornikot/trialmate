"""Shared HTTP helpers."""
from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle when installed (fixes python.org macOS installs), else system defaults."""
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

"""Shared HTTP helpers."""
from __future__ import annotations

import ssl
from functools import lru_cache


# Cloudflare-fronted APIs (Photon) reject Python's default "Python-urllib" user agent with error 1010.
USER_AGENT = "TrialMate/0.1 (HackHarvard 2026; +https://github.com/egornikot)"


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """System CA store plus certifi's bundle when installed.

    System store: corporate/school proxies and Windows-trusted roots. certifi: fixes python.org macOS
    installs that ship without system roots. Trusting the union keeps both cases working.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx

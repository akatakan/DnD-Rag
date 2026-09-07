"""Uvicorn entrypoint.

The LAN bind is intentional: players join from their own devices over the local
network (see README). Reload is not — it used to be hardcoded on, which meant
the documented start command ran a development server with a file watcher.

Behind a reverse proxy set FORWARDED_ALLOW_IPS to the trusted proxy IP/CIDR, as
docs/production-operations.md requires; without it PUBLIC_MODE sees the proxy's
plaintext hop instead of the client's real https scheme and rejects requests.
"""

import os

import uvicorn


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "").strip()
    if forwarded_allow_ips == "*":
        raise SystemExit(
            "FORWARDED_ALLOW_IPS='*' her istemcinin scheme'i taklit etmesine "
            "izin verir; guvenilen proxy IP/CIDR degerini verin."
        )
    uvicorn.run(
        "api.app:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=env_bool("API_RELOAD", False),
        proxy_headers=bool(forwarded_allow_ips),
        forwarded_allow_ips=forwarded_allow_ips or None,
    )

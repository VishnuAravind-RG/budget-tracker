"""Single-user bearer-token auth. One shared secret, constant-time compared."""

import os
import secrets

from fastapi import Header, HTTPException

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "").strip()

if not AUTH_TOKEN:
    raise RuntimeError(
        "AUTH_TOKEN is not set. Copy .env.example to .env and put a long random "
        "string in it (e.g. `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`)."
    )


def require_token(authorization: str = Header(default="")) -> None:
    """Dependency: every route except /health requires `Authorization: Bearer <token>`."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, AUTH_TOKEN):
        raise HTTPException(401, "Invalid or missing token")

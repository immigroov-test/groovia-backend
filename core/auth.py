# auth.py
# JWT validation for Supabase-issued user tokens.
# Local verification (no network round-trip) using SUPABASE_JWT_SECRET.

import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config

logger = logging.getLogger("immigroov.auth")

# auto_error=False so we can build optional-auth endpoints (guest mode still works)
_bearer = HTTPBearer(auto_error=False)

# Supabase publishes its asymmetric signing keys (ES256/RS256) here. New projects
# sign tokens with these by default; older projects use the shared HS256 secret.
# PyJWKClient is lazy + caches the key set, so HS256-only setups never hit the network.
_JWKS_URL = f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
_jwks_client = PyJWKClient(_JWKS_URL, cache_keys=True, lifespan=3600)


@dataclass(frozen=True)
class AuthUser:
    """A verified Supabase user. Only what we actually need downstream."""
    id: str           # auth.users.id (UUID as string)
    email: str
    role: str         # supabase auth role, e.g. 'authenticated'


def _decode(token: str) -> dict:
    """Verify a Supabase JWT, supporting BOTH signing schemes:
      - HS256: legacy shared secret (SUPABASE_JWT_SECRET)
      - ES256/RS256: asymmetric keys fetched from the project JWKS endpoint
    The algorithm is read from the (unverified) token header and the matching
    verification path is chosen, so the same backend works regardless of whether
    the project has migrated to asymmetric JWT signing keys."""
    alg = jwt.get_unverified_header(token).get("alg", "HS256")
    common = dict(
        audience="authenticated",   # Supabase tags logged-in users with this audience
        options={"require": ["exp", "sub"]},
    )
    if alg.startswith("HS"):
        return jwt.decode(token, config.SUPABASE_JWT_SECRET, algorithms=["HS256"], **common)
    # Asymmetric: verify against the project's published public key.
    signing_key = _jwks_client.get_signing_key_from_jwt(token).key
    return jwt.decode(token, signing_key, algorithms=["ES256", "RS256"], **common)


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthUser:
    """Required-auth dependency. Raises 401 if missing/invalid token."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        payload = _decode(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid JWT (alg=%s): %s", _safe_alg(creds.credentials), e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return AuthUser(
        id=payload["sub"],
        email=payload.get("email", ""),
        role=payload.get("role", "authenticated"),
    )


def _safe_alg(token: str) -> str:
    """Best-effort read of the token's alg for diagnostics — never raises."""
    try:
        return jwt.get_unverified_header(token).get("alg", "?")
    except Exception:
        return "?"


def get_current_user_optional(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[AuthUser]:
    """Optional-auth dependency. Returns None for guests, AuthUser when authenticated.
    Use on endpoints that work both ways (e.g. /chat allows guest threads)."""
    if creds is None:
        return None
    try:
        payload = _decode(creds.credentials)
    except jwt.InvalidTokenError:
        return None
    return AuthUser(
        id=payload["sub"],
        email=payload.get("email", ""),
        role=payload.get("role", "authenticated"),
    )


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Dependency that raises 403 unless the caller's profiles.role is 'admin'."""
    import db as _db  # local import avoids circular dependency at module level
    if _db.get_profile_role(user.id) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def attach_user_to_request(request: Request, user: Optional[AuthUser]) -> None:
    """Stash the user on request.state so handlers/middleware can read it."""
    request.state.user = user

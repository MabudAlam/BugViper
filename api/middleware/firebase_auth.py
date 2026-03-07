import logging

import firebase_admin.auth
from starlette.datastructures import State
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Routes that do NOT require a Firebase token.
# Prefix matching — any path that starts with one of these is public.
PUBLIC_PREFIXES: tuple[str, ...] = (
    # OpenAPI docs
    "/docs",
    "/redoc",
    "/openapi.json",
    # GitHub App webhooks use HMAC signature verification, not Firebase tokens
    "/api/v1/webhook",
    # Health / readiness
    "/health",
    # Note: /auth/login and /auth/ensure are NOT public.
    # Firebase auth happens client-side first — the client always has a token
    # before calling these endpoints.
)


class FirebaseAuthMiddleware:
    """
    Pure ASGI middleware that verifies Firebase ID tokens on every request.

    Using a pure ASGI middleware (not BaseHTTPMiddleware) so that FastAPI's
    BackgroundTasks are not swallowed — BaseHTTPMiddleware has a known
    limitation where background tasks do not run.

    Flow:
        1. Non-HTTP scope (lifespan, websocket) → pass through unchanged.
        2. OPTIONS requests → pass through (CORS preflight has no auth header).
        3. Public prefixes → pass through.
        4. Read ``Authorization: Bearer <token>`` header.
        5. Verify with Firebase Admin SDK.
        6. On success  → set ``scope["state"].user``, continue.
        7. On failure  → return 401 JSON immediately.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # ── 0. Only inspect HTTP requests ───────────────────────────────────
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "")
        path: str = scope.get("path", "")

        # ── 1. CORS preflight bypass ─────────────────────────────────────────
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # ── 2. Public routes bypass auth ─────────────────────────────────────
        if path.startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # ── 3. Extract token from Authorization header ────────────────────────
        headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        raw_auth = headers.get(b"authorization", b"")
        auth_header = raw_auth.decode("utf-8", errors="ignore")

        if not auth_header.startswith("Bearer "):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or malformed Authorization header. Expected: Bearer <token>"},
            )
            await response(scope, receive, send)
            return

        token = auth_header[len("Bearer "):].strip()
        if not token:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Empty token in Authorization header"},
            )
            await response(scope, receive, send)
            return

        # ── 4. Verify with Firebase Admin SDK ─────────────────────────────────
        try:
            decoded = firebase_admin.auth.verify_id_token(token)
        except firebase_admin.auth.ExpiredIdTokenError:
            logger.warning("Expired Firebase token from %s %s", method, path)
            response = JSONResponse(
                status_code=401,
                content={"detail": "Token has expired. Please sign in again."},
            )
            await response(scope, receive, send)
            return
        except firebase_admin.auth.RevokedIdTokenError:
            logger.warning("Revoked Firebase token from %s %s", method, path)
            response = JSONResponse(
                status_code=401,
                content={"detail": "Token has been revoked. Please sign in again."},
            )
            await response(scope, receive, send)
            return
        except firebase_admin.auth.InvalidIdTokenError as exc:
            logger.warning("Invalid Firebase token: %s", exc)
            response = JSONResponse(
                status_code=401,
                content={"detail": "Invalid token."},
            )
            await response(scope, receive, send)
            return
        except Exception as exc:
            logger.exception("Unexpected error verifying Firebase token: %s", exc)
            response = JSONResponse(
                status_code=401,
                content={"detail": "Token verification failed."},
            )
            await response(scope, receive, send)
            return

        # ── 5. Attach decoded claims to scope state ───────────────────────────
        # Starlette's Request.state reads from scope["state"] (a State instance).
        # Uvicorn pre-initialises scope["state"] as a plain dict {}, so we must
        # wrap it in a State object before setting attributes on it.
        # Any route handler can now do: uid = request.state.user["uid"]
        existing = scope.get("state", {})
        if not isinstance(existing, State):
            scope["state"] = State(existing)
        scope["state"].user = decoded
        logger.debug("Authenticated uid=%s → %s %s", decoded.get("uid"), method, path)

        await self.app(scope, receive, send)

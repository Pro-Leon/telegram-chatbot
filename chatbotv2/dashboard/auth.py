import logging
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from db.postgres import (
    clean_expired_sessions,
    init_pool,
)
from db.postgres import (
    create_session as create_session_db,
)
from db.postgres import (
    delete_session as delete_session_db,
)
from db.postgres import (
    get_session as get_session_db,
)

logger = logging.getLogger("chatbotv2.dashboard.auth")

_env = Environment(
    loader=FileSystemLoader("chatbotv2/dashboard/templates"),
    autoescape=True,
)


async def _ensure_pool():
    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Pool already initialized or init failed: %s", exc)


async def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + 3600 * 8
    await _ensure_pool()
    await create_session_db(token, username, expires_at)
    return token


async def verify_session(token: str | None) -> dict | None:
    if not token:
        return None
    await _ensure_pool()
    row = await get_session_db(token)
    if row:
        return {"username": row["username"]}
    return None


async def destroy_session(token: str | None) -> None:
    if token:
        await _ensure_pool()
        await delete_session_db(token)


async def get_session_user(request: Request) -> dict | None:
    token = request.cookies.get("session")
    return await verify_session(token)


async def require_auth(request: Request) -> dict:
    session = await get_session_user(request)
    if not session:
        # Browser page navigations get the designed 401 page; API/fetch
        # callers keep the JSON contract (tests + realtime client rely on
        # the 401 status, never on the body shape).
        accept = request.headers.get("accept", "")
        wants_page = (
            request.method == "GET"
            and not request.url.path.startswith("/api")
            and "text/html" in accept
        )
        if wants_page:
            raise AuthRequiredPage(request.url.path)
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )
    return session


class AuthRequiredPage(Exception):
    """Unauthenticated browser page visit — rendered as the 401 page."""

    def __init__(self, path: str):
        super().__init__(path)
        self.path = path if path.startswith("/") else "/dashboard/overview"


def render_auth_required(request: Request, exc: AuthRequiredPage) -> HTMLResponse:
    """Render the designed 401 page (fail-open to login on any error)."""
    try:
        return render(request, "401.html", next_url=exc.path)
    except Exception:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/login", status_code=303)


async def cleanup_expired_sessions() -> None:
    await _ensure_pool()
    removed = await clean_expired_sessions()
    if removed:
        logger.info("Cleaned up %d expired sessions", removed)


def render(request: Request, template: str, **context) -> HTMLResponse:
    ctx = {"request": request, **context}
    tmpl = _env.get_template(template)
    html = tmpl.render(**ctx)
    return HTMLResponse(html)

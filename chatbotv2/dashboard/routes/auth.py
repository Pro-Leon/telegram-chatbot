"""Authentication routes (login, logout, root redirect)."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from chatbotv2.config import get_settings
from chatbotv2.dashboard.auth import (
    create_session,
    destroy_session,
    get_session_user,
    render,
)

_settings = get_settings()

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_root(request: Request):
    session = await get_session_user(request)
    if not session:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/dashboard/overview")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    next_url = request.query_params.get("next", "")
    if not next_url.startswith("/"):
        next_url = ""
    return render(request, "login.html", next_url=next_url)


def _safe_next_url(value: str | None) -> str:
    """Local dashboard paths only — never external redirects."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        return value[:200]
    return "/dashboard/overview"


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
):
    if next is None:
        next = request.query_params.get("next", "")
    if password != _settings.dashboard_admin_password:
        return render(request, "login.html", error="Invalid credentials", next_url=next if next.startswith("/") else "")
    token = await create_session(username)
    response = RedirectResponse(url=_safe_next_url(next), status_code=303)
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    await destroy_session(token)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response

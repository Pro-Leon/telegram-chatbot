"""HTML page routes."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse

from chatbotv2.config import get_settings
from chatbotv2.dashboard.auth import render, require_auth
from db import fangate as fdb
from db.postgres import (
    get_all_personas,
    get_pending_queue_items,
    get_pool,
    get_recent_messages,
    get_user_persona,
    get_user_profile_with_embedding,
)

_settings = get_settings()

router = APIRouter()


async def _resolve_creator_id() -> int:
    """Resolve the application creator ID for DropFans pages."""
    from commerce.single_creator import resolve_single_application_creator

    ctx = await resolve_single_application_creator()
    if ctx.creator_id is not None:
        return ctx.creator_id
    any_id = await fdb.get_any_creator_id_with_integration()
    return any_id if any_id is not None else 0


@router.get("/dashboard/personas", response_class=HTMLResponse)
async def dashboard_personas(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "personas.html")


@router.get("/dashboard/persona-studio", response_class=HTMLResponse)
async def dashboard_persona_studio(request: Request, auth: dict = Depends(require_auth)):
    # Phase 89 Studio — creator resolution for initial load
    creator_id = await _resolve_creator_id()
    return render(request, "persona_studio.html", creator_id=creator_id)


@router.get("/dashboard/overview", response_class=HTMLResponse)
async def dashboard_overview(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "overview.html")


@router.get("/dashboard/queue", response_class=HTMLResponse)
async def dashboard_queue(request: Request, auth: dict = Depends(require_auth)):
    # M7 (B6): creator-scoped (previously called without creator_id, which
    # raises under the strict helper). Fail closed with an empty list.
    creator_id = await _resolve_creator_id()
    items = (
        await get_pending_queue_items(limit=50, creator_id=creator_id)
        if creator_id
        else []
    )
    return render(request, "queue.html", items=items)


@router.get("/dashboard/users", response_class=HTMLResponse)
async def dashboard_users(request: Request, auth: dict = Depends(require_auth)):
    # M7 (B6): creator-scoped fan list. The users table has no creator_id
    # column (structural), so scope via activity under this creator. Fans
    # with only legacy NULL-creator rows are fail-closed (not listed).
    creator_id = await _resolve_creator_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetch(
            "SELECT id, username, first_name, message_count, last_seen FROM users u "
            "WHERE EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.id AND m.creator_id = $1) "
            "OR EXISTS (SELECT 1 FROM operator_queue q WHERE q.user_id = u.id AND q.creator_id = $1) "
            "ORDER BY last_seen DESC LIMIT 100",
            creator_id,
        )
    return render(request, "users.html", users=jsonable_encoder([dict(r) for r in users]))


@router.get("/dashboard/chats", response_class=HTMLResponse)
async def dashboard_chats(
    request: Request,
    chat_id: int = Query(default=None),
    auth: dict = Depends(require_auth),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # M7 (B6): creator-scoped chat list (CTEs + membership, all predicated).
        creator_id = await _resolve_creator_id()
        rows = await conn.fetch("""
            WITH last_msgs AS (
                SELECT DISTINCT ON (user_id) user_id, content, created_at
                FROM messages
                WHERE creator_id = $1
                ORDER BY user_id, created_at DESC
            ),
            unread_counts AS (
                SELECT user_id, COUNT(*) AS cnt
                FROM messages
                WHERE direction = 'inbound' AND sent_at IS NULL AND creator_id = $1
                GROUP BY user_id
            )
            SELECT
                u.id, u.username, u.first_name, u.message_count, u.last_seen,
                lm.content AS last_message,
                lm.created_at AS last_message_at,
                COALESCE(uc.cnt, 0) AS unread
            FROM users u
            LEFT JOIN last_msgs lm ON lm.user_id = u.id
            LEFT JOIN unread_counts uc ON uc.user_id = u.id
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.user_id = u.id AND m.creator_id = $1)
               OR EXISTS (SELECT 1 FROM operator_queue q WHERE q.user_id = u.id AND q.creator_id = $1)
            ORDER BY u.last_seen DESC
            LIMIT 100
        """, creator_id)
        dialogs = jsonable_encoder([dict(r) for r in rows])

        active_chat = None
        if chat_id:
            row = await conn.fetchrow(
                "SELECT first_name, username FROM users WHERE id = $1", chat_id
            )
            dialog_name = (
                row["first_name"] or row["username"] or str(chat_id) if row else str(chat_id)
            )
            msg_rows = await conn.fetch(
                "SELECT id, direction, content, sent_at, created_at "
                "FROM messages WHERE user_id = $1 AND creator_id = $2 ORDER BY created_at ASC LIMIT 100",
                chat_id,
                creator_id,
            )
            unread = (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM messages WHERE user_id = $1 AND creator_id = $2 "
                    "AND direction = 'inbound' AND sent_at IS NULL",
                    chat_id,
                    creator_id,
                )
                or 0
            )
            active_chat = {
                "id": chat_id,
                "name": dialog_name,
                "unread_count": unread,
                "messages": [
                    {
                        "id": r["id"],
                        "direction": r["direction"],
                        "text": r["content"],
                        "date": r["created_at"].isoformat() if r["created_at"] else None,
                        "sender_id": chat_id if r["direction"] == "inbound" else None,
                        "is_outgoing": r["direction"] == "outbound",
                        "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                    }
                    for r in msg_rows
                ],
            }

    return render(
        request,
        "chats.html",
        dialogs=dialogs,
        active_chat=active_chat,
        active_chat_id=chat_id,
    )


@router.get("/dashboard/chat/{dialog_id}/embed", response_class=HTMLResponse)
async def dashboard_chat_embed(
    request: Request, dialog_id: int, auth: dict = Depends(require_auth)
):
    """Lightweight chat page for iframe embedding — no dashboard layout."""
    # M7 (B6): creator-scoped message reads (name lookup stays structural —
    # the users table has no creator column).
    creator_id = await _resolve_creator_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT first_name, username FROM users WHERE id = $1", dialog_id)
        dialog_name = (
            row["first_name"] or row["username"] or str(dialog_id) if row else str(dialog_id)
        )
        msg_rows = await conn.fetch(
            "SELECT id, direction, content, sent_at, created_at "
            "FROM messages WHERE user_id = $1 AND creator_id = $2 ORDER BY created_at ASC LIMIT 100",
            dialog_id,
            creator_id,
        )
        unread = (
            await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE user_id = $1 AND creator_id = $2 "
                "AND direction = 'inbound' AND sent_at IS NULL",
                dialog_id,
                creator_id,
            )
            or 0
        )
        messages = [
            {
                "id": r["id"],
                "direction": r["direction"],
                "text": r["content"],
                "date": r["created_at"].isoformat() if r["created_at"] else None,
                "is_outgoing": r["direction"] == "outbound",
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
            }
            for r in msg_rows
        ]

    from chatbotv2.dashboard.auth import _env

    tmpl = _env.get_template("chat_embed.html")
    html = tmpl.render(
        request=request,
        dialog_id=dialog_id,
        dialog_name=dialog_name,
        messages=messages,
        unread_count=unread,
        creator_id=creator_id,
    )
    return HTMLResponse(html)


@router.get("/dashboard/chat/{dialog_id}", response_class=HTMLResponse)
async def dashboard_chat(request: Request, dialog_id: int, auth: dict = Depends(require_auth)):
    # M7 (B6): creator-scoped message/queue reads.
    creator_id = await _resolve_creator_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT first_name, username FROM users WHERE id = $1", dialog_id)
        dialog_name = (
            row["first_name"] or row["username"] or str(dialog_id) if row else str(dialog_id)
        )
        rows = await conn.fetch(
            "SELECT id, direction, content, sent_at, created_at, media_type, media_path, fangate_media_id, "
            "was_auto_approved, was_edited, confidence_score, draft_content "
            "FROM messages WHERE user_id = $1 AND creator_id = $2 ORDER BY created_at ASC LIMIT 100",
            dialog_id,
            creator_id,
        )
        unread = (
            await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE user_id = $1 AND creator_id = $2 AND direction = 'inbound' AND sent_at IS NULL",
                dialog_id,
                creator_id,
            )
            or 0
        )
        # AI-native: fetch pending suggestions for this dialog (queue context)
        pending_suggestions = await conn.fetch(
            "SELECT id, draft_content, confidence_score, flags FROM operator_queue "
            "WHERE user_id = $1 AND creator_id = $2 AND status = 'pending' ORDER BY created_at DESC LIMIT 5",
            dialog_id,
            creator_id,
        )
    return render(
        request,
        "chat.html",
        dialog_id=dialog_id,
        dialog_name=dialog_name,
        unread_count=unread,
        messages=[
            {
                "id": r["id"],
                "direction": r["direction"],
                "text": r["content"],
                "date": r["created_at"].isoformat() if r["created_at"] else None,
                "sender_id": dialog_id if r["direction"] == "inbound" else None,
                "is_outgoing": r["direction"] == "outbound",
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                "media_type": r["media_type"],
                "media_path": r["media_path"],
                "fangate_media_id": r["fangate_media_id"],
                "was_auto_approved": r["was_auto_approved"] or False,
                "was_edited": r["was_edited"] or False,
                "confidence_score": r["confidence_score"],
                "draft_content": r["draft_content"],
            }
            for r in rows
        ],
        pending_suggestions=[dict(s) for s in pending_suggestions],
    )


@router.get("/dashboard/settings", response_class=HTMLResponse)
async def dashboard_settings(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "settings.html", settings=dict(_settings.model_dump()))


@router.get("/dashboard/analytics", response_class=HTMLResponse)
async def dashboard_analytics(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "analytics.html", current_user=auth.get("username"))


@router.get("/dashboard/profile/{user_id}", response_class=HTMLResponse)
async def dashboard_profile(request: Request, user_id: int, auth: dict = Depends(require_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, first_name, message_count, funnel_stage, is_blocked, do_not_auto_reply, notes FROM users WHERE id = $1",
            user_id,
        )
        if not row:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="User not found")
    creator_id = await _resolve_creator_id()
    messages = await get_recent_messages(user_id, limit=50, creator_id=creator_id)
    profile = await get_user_profile_with_embedding(user_id)
    personas = await get_all_personas(creator_id=creator_id)
    user_persona = await get_user_persona(user_id, creator_id=creator_id)
    return render(
        request,
        "profile.html",
        user=jsonable_encoder(dict(row)),
        messages=jsonable_encoder(messages),
        profile=profile or {},
        personas=personas,
        user_persona=user_persona,
    )


@router.get("/dashboard/fangate", response_class=HTMLResponse)
async def dashboard_fangate(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "fangate.html", creator_id=creator_id)


@router.get("/dashboard/dropfans", response_class=HTMLResponse)
async def dashboard_dropfans_overview(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "dropfans_overview.html", creator_id=creator_id)


@router.get("/dashboard/dropfans/settings", response_class=HTMLResponse)
async def dashboard_dropfans_settings(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "dropfans_settings.html", creator_id=creator_id)


@router.get("/dashboard/drops", response_class=HTMLResponse)
async def dashboard_drops(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "drops.html", creator_id=creator_id)


@router.get("/dashboard/posts", response_class=HTMLResponse)
async def dashboard_posts(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "posts.html", creator_id=creator_id)


@router.get("/dashboard/earnings", response_class=HTMLResponse)
async def dashboard_earnings(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "earnings.html", creator_id=creator_id)


@router.get("/dashboard/links", response_class=HTMLResponse)
async def dashboard_links(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "links.html", creator_id=creator_id)


@router.get("/dashboard/vault", response_class=HTMLResponse)
async def dashboard_vault(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "vault.html", creator_id=creator_id)


@router.get("/dashboard/notifications", response_class=HTMLResponse)
async def dashboard_notifications(request: Request, auth: dict = Depends(require_auth)):
    creator_id = await _resolve_creator_id()
    return render(request, "notifications.html", creator_id=creator_id)


@router.get("/dashboard/segments", response_class=HTMLResponse)
async def dashboard_segments(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "segments.html")


@router.get("/dashboard/followups", response_class=HTMLResponse)
async def dashboard_followups(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "followups.html")


@router.get("/dashboard/creators", response_class=HTMLResponse)
async def dashboard_creators(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "creators.html")


@router.get("/dashboard/crm", response_class=HTMLResponse)
async def dashboard_crm(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "crm.html")


@router.get("/dlq", response_class=HTMLResponse)
async def dashboard_dlq(request: Request, auth: dict = Depends(require_auth)):
    return render(request, "dlq.html")

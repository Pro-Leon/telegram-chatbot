"""MTProto Chatbot Dashboard — Application Composition Layer.

This module creates the FastAPI application and wires together all routes,
middleware, startup/shutdown hooks, and static file mounting.

Route implementations live in chatbotv2/dashboard/routes/.
Shared validation helpers live in chatbotv2/dashboard/dependencies.py.
Pydantic request/response models live in chatbotv2/dashboard/schemas.py.
CSV export helpers live in chatbotv2/dashboard/csv_helpers.py.
"""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chatbotv2.dashboard.csv_helpers import (  # noqa: F401 — re-exports for test compatibility
    build_activity_csv_rows as _build_activity_csv_rows,
)
from chatbotv2.dashboard.csv_helpers import (  # noqa: F401 — re-exports for test compatibility
    build_conversations_csv_rows as _build_conversations_csv_rows,
)
from chatbotv2.dashboard.csv_helpers import (  # noqa: F401 — re-exports for test compatibility
    sanitize_csv_value as _sanitize_csv_value,
)
from chatbotv2.dashboard.auth import AuthRequiredPage, render_auth_required
from core.event_bus import publish_event  # noqa: F401
from core.middleware import RequestIDMiddleware

# Re-export symbols so existing test patches (chatbotv2.dashboard.app.X) continue to work.
# Tests use `patch("chatbotv2.dashboard.app.get_pool", ...)` etc.
from db.postgres import (  # noqa: F401
    assign_conversation_tag,
    cancel_scheduled_message_rich,
    count_conversation_notes,
    count_scheduled_messages,
    create_conversation_note,
    create_conversation_tag,
    delete_conversation_note,
    delete_conversation_tag,
    get_conversation_activity,
    get_conversation_activity_export_rows,
    get_conversation_detail,
    get_conversation_note,
    get_conversation_tag,
    get_conversations_analytics,
    get_conversations_export_rows,
    get_dashboard_analytics,
    get_operator_list,
    get_pool,
    get_scheduled_message_with_user,
    get_scheduled_stats,
    list_conversation_notes,
    list_conversation_tags,
    list_scheduled_messages_with_users,
    list_user_conversation_tags,
    remove_conversation_tag,
    search_conversation_notes,
    search_messages,
    search_users,
    update_conversation_note,
    upsert_conversation_attention,
)

logger = logging.getLogger("chatbotv2.dashboard")

app = FastAPI(title="MTProto Chatbot Dashboard")
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(AuthRequiredPage)
async def auth_required_page_handler(request, exc: AuthRequiredPage):
    from fastapi.responses import HTMLResponse

    response = render_auth_required(request, exc)
    if isinstance(response, HTMLResponse):
        response.status_code = 401
    return response
app.mount(
    "/static",
    StaticFiles(directory="chatbotv2/dashboard/static"),
    name="static",
)


_subscriber_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global _subscriber_task
    from db.postgres import init_pool, verify_schema
    from db.redis import ensure_consumer_group

    await init_pool()
    await verify_schema()
    await ensure_consumer_group()

    from chatbotv2.dashboard.auth import cleanup_expired_sessions

    await cleanup_expired_sessions()

    from chatbotv2.dashboard.event_subscriber import start_event_subscriber
    from core.config import get_settings as get_core_settings

    core_settings = get_core_settings()
    if core_settings.enable_websocket:
        _subscriber_task = asyncio.create_task(start_event_subscriber())
        logger.info("WebSocket event subscriber started")
    else:
        logger.info("WebSocket disabled, event subscriber not started")

    logger.info("Dashboard started")


@app.on_event("shutdown")
async def shutdown():
    global _subscriber_task
    from db.postgres import close_pool
    from db.redis import close_redis

    if _subscriber_task is not None:
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass
        _subscriber_task = None

    await close_pool()
    await close_redis()
    logger.info("Dashboard stopped")


# ── Register all route modules ──────────────────────────────────────────────

from chatbotv2.dashboard.routes.analytics import router as analytics_router
from chatbotv2.dashboard.routes.attention import router as attention_router
from chatbotv2.dashboard.routes.auth import router as auth_router
from chatbotv2.dashboard.routes.bulk_ops import router as bulk_ops_router
from chatbotv2.dashboard.routes.crm import router as crm_router
from chatbotv2.dashboard.routes.dialogs import router as dialogs_router
from chatbotv2.dashboard.routes.dlq import router as dlq_router
from chatbotv2.dashboard.routes.export import router as export_router
from chatbotv2.dashboard.routes.fangate import (
    register_fangate_exception_handlers,
)
from chatbotv2.dashboard.routes.fangate import (
    router as fangate_router,
)
from chatbotv2.dashboard.routes.followups import router as followups_router
from chatbotv2.dashboard.routes.health import router as health_router
from chatbotv2.dashboard.routes.messages import router as messages_router
from chatbotv2.dashboard.routes.notes import router as notes_router
from chatbotv2.dashboard.routes.operators import router as operators_router
from chatbotv2.dashboard.routes.pages import router as pages_router
from chatbotv2.dashboard.routes.personas import router as personas_router
from chatbotv2.dashboard.routes.queue import router as queue_router
from chatbotv2.dashboard.routes.search import router as search_router
from chatbotv2.dashboard.routes.ai_intel import router as ai_intel_router
from chatbotv2.dashboard.routes.live import router as live_router
from chatbotv2.dashboard.routes.segments import router as segments_router
from chatbotv2.dashboard.routes.settings import router as settings_router
from chatbotv2.dashboard.routes.tags import router as tags_router
from chatbotv2.dashboard.routes.users import router as users_router
from chatbotv2.dashboard.routes.vault import router as vault_router
from chatbotv2.dashboard.routes.ws import router as ws_router
from chatbotv2.dashboard.routes.lab import router as lab_router
from chatbotv2.dashboard.routes.trace import router as trace_router

app.include_router(health_router)
app.include_router(dlq_router)
app.include_router(ws_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(messages_router)
app.include_router(queue_router)
app.include_router(dialogs_router)
app.include_router(personas_router)
app.include_router(settings_router)
app.include_router(operators_router)
app.include_router(export_router)
app.include_router(analytics_router)
app.include_router(fangate_router)
register_fangate_exception_handlers(app)
app.include_router(followups_router)
app.include_router(bulk_ops_router)
app.include_router(crm_router)
app.include_router(notes_router)
app.include_router(tags_router)
app.include_router(attention_router)
app.include_router(search_router)
app.include_router(ai_intel_router)
app.include_router(pages_router)
app.include_router(vault_router)
app.include_router(live_router)
app.include_router(segments_router)
app.include_router(lab_router)
app.include_router(trace_router)


if __name__ == "__main__":
    import uvicorn

    from core.config import get_settings as get_core_settings
    from core.logging_config import setup_logging

    _core_settings = get_core_settings()
    setup_logging(structured=_core_settings.structured_logging)
    uvicorn.run(app, host="0.0.0.0", port=1010)

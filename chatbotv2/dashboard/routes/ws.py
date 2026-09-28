"""WebSocket route."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time events.

    Auth: Checks session cookie from the HTTP upgrade request.
    """
    from chatbotv2.dashboard.auth import verify_session
    from chatbotv2.dashboard.ws_manager import get_manager

    session_token = websocket.cookies.get("session")
    if not session_token:
        await websocket.close(code=4001, reason="Missing session cookie")
        return

    user = await verify_session(session_token)
    if not user:
        await websocket.close(code=4001, reason="Invalid session")
        return

    manager = get_manager()
    # Resolve creator for this dashboard session (single-creator deployment)
    creator_ids = set()
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        ctx = await resolve_single_application_creator()
        if ctx.status == SingleCreatorStatus.READY and ctx.creator_id is not None:
            creator_ids = {ctx.creator_id}
    except Exception:
        creator_ids = set()

    await manager.connect(websocket, is_global=True, creator_ids=creator_ids)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"event_type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket connection error", exc_info=True)
    finally:
        await manager.disconnect(websocket)

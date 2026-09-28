"""Dashboard CRM surface tests (pure explain paths + auth + registration).

Explain endpoints are pure deterministic functions (no DB); fan endpoints
need live DB and are covered by integration runs, not here.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _ready_creator():
    from commerce.single_creator import SingleCreatorStatus

    ctx = type("Ctx", (), {})()
    ctx.status = SingleCreatorStatus.READY
    ctx.creator_id = 7
    return ctx


def _override_auth():
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    app.dependency_overrides[require_auth] = lambda: {"username": "admin"}


def _clear_auth():
    from chatbotv2.dashboard.app import app

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_crm_requires_authentication():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            "/api/crm/fan/1/relationship",
            "/api/crm/fan/1/intimacy",
            "/api/crm/fan/1/boundaries",
            "/api/crm/fan/1/commerce",
            "/api/crm/fan/1/opportunity",
            "/api/crm/opportunities/recent",
            "/api/crm/catalog",
        ):
            resp = await client.get(path)
            assert resp.status_code == 401, path


@pytest.mark.asyncio
async def test_crm_explain_desire_warm_is_not_commercial():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    _override_auth()
    try:
        with patch(
            "chatbotv2.dashboard.routes.crm.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_ready_creator(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/crm/explain/desire",
                    json={
                        "relationship_state": "warm",
                        "primary_intent": "casual_chat",
                        "purchase_intent": 0.1,
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["stage"] == "interest"
        assert "interest:warmth" in body["evidence"]
    finally:
        _clear_auth()


@pytest.mark.asyncio
async def test_crm_explain_temperature_shape():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    _override_auth()
    try:
        with patch(
            "chatbotv2.dashboard.routes.crm.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_ready_creator(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/crm/explain/temperature",
                    json={
                        "relationship_score": 0.65,
                        "desire_stage": "interest",
                        "purchase_intent": 0.1,
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["level"] in ("cold", "warm", "hot")
        assert 0.0 <= body["score"] <= 1.0
        assert "sales_fatigue" in body
    finally:
        _clear_auth()


@pytest.mark.asyncio
async def test_crm_explain_readiness_qualified_hot():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    _override_auth()
    try:
        with patch(
            "chatbotv2.dashboard.routes.crm.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_ready_creator(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/crm/explain/readiness",
                    json={
                        "desire_stage": "qualification",
                        "temperature": "hot",
                        "purchase_intent": 0.6,
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["readiness"]["offer_readiness"] == "ready"
        assert body["readiness"]["available"] is True
        assert body["offer_readiness_legacy"] == "ready"
        assert body["sales_window"] == "open"
        assert body["warming"]["level"] in (
            "cold",
            "warm",
            "hot",
            "cooldown",
            "aftercare",
            "not_available",
        )
    finally:
        _clear_auth()


@pytest.mark.asyncio
async def test_crm_explain_objective_selects_eligible():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    _override_auth()
    try:
        with patch(
            "chatbotv2.dashboard.routes.crm.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_ready_creator(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/crm/explain/objective",
                    json={
                        "desire": "interest",
                        "temperature": "warm",
                        "sales_window": "building",
                        "offer_readiness": "test_interest",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        eligible = {c["objective"] for c in body["candidates"] if c["eligible"]}
        assert body["selected"] in eligible
    finally:
        _clear_auth()


@pytest.mark.asyncio
async def test_crm_explain_qualification_empty():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    _override_auth()
    try:
        with patch(
            "chatbotv2.dashboard.routes.crm.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_ready_creator(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/crm/explain/qualification",
                    json={"preferences": {}, "purchase_history": [], "recent_offers": []},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert "format_preference" in body["missing_facts"]
        assert body["known_facts"] == []
    finally:
        _clear_auth()


def test_crm_routes_registered():
    from chatbotv2.dashboard.app import app

    paths = set()

    def _collect(routes):
        for route in routes:
            path = getattr(route, "path", "")
            if path:
                paths.add(path)
            inner = getattr(route, "original_router", None)
            inner_routes = getattr(inner, "routes", None) if inner is not None else None
            if inner_routes:
                _collect(inner_routes)
            for nested in getattr(route, "routes", []) or []:
                npath = getattr(nested, "path", "")
                if npath:
                    paths.add(npath)

    _collect(app.routes)
    for expected in (
        "/api/crm/fan/{user_id}/relationship",
        "/api/crm/fan/{user_id}/intimacy",
        "/api/crm/fan/{user_id}/boundaries",
        "/api/crm/fan/{user_id}/commerce",
        "/api/crm/fan/{user_id}/opportunity",
        "/api/crm/fan/{user_id}/handoff/clear",
        "/api/crm/opportunities/recent",
        "/api/crm/catalog",
        "/api/crm/explain/desire",
        "/api/crm/explain/temperature",
        "/api/crm/explain/readiness",
        "/api/crm/explain/objective",
        "/api/crm/explain/qualification",
        "/dashboard/crm",
    ):
        assert expected in paths, expected

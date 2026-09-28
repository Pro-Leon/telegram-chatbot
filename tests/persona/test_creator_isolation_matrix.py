import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.unit]

def test_forbidden_keys_include_player():
    from chatbotv2.dashboard.routes.personas import _contains_forbidden_commerce
    for key in ["player_name", "player_identity", "player_dialogue", "player_action", "listener_name", "fan_name"]:
        assert _contains_forbidden_commerce({key: "value"}) == key, key
        assert _contains_forbidden_commerce({"nested": {key: "x"}}) == key
    # commerce still forbidden
    assert _contains_forbidden_commerce({"product_id": 1}) == "product_id"
    assert _contains_forbidden_commerce({"price_minor": 100}) == "price_minor"

@pytest.mark.asyncio
async def test_get_cross_creator_forbidden():
    from chatbotv2.dashboard.routes.personas import _verify_persona_ownership
    from fastapi import HTTPException
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"creator_id": 2})
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool):
        try:
            await _verify_persona_ownership(10, 1, {"username": "a"})
            assert False, "should 403"
        except HTTPException as e:
            assert e.status_code == 403

@pytest.mark.asyncio
async def test_update_cross_creator_forbidden():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth
    # Mock _verify to raise 403 for cross
    with patch("chatbotv2.dashboard.routes.personas._verify_persona_ownership", new_callable=AsyncMock) as mock_verify:
        from fastapi import HTTPException
        mock_verify.side_effect = HTTPException(status_code=403, detail="Creator isolation")
        with patch("db.postgres.get_pool"):
            client = TestClient(app)
            # Need to send Form data
            resp = client.put("/api/personas/1", data={"name": "x", "instructions": "y", "is_default": "false", "creator_id": "1", "metadata": "{}"})
            # Should be 403
            assert resp.status_code == 403
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_promote_cross_creator_forbidden():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"creator_id": 2, "is_default": False})
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool):
        client = TestClient(app)
        resp = client.post("/api/personas/1/promote", data={"creator_id": "1"})
        assert resp.status_code == 403
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_preview_cross_creator_forbidden():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    # First fetch for persona row, second for creator check
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": 1, "name": "Sunny", "instructions": "You are Sunny", "metadata": {}, "version": 1},
        {"creator_id": 2},
    ])
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool):
        with patch("memory.creator_persona.render_persona_block", return_value="x"), patch("memory.creator_persona.render_compact_persona_block", return_value="y"):
            client = TestClient(app)
            resp = client.post("/api/personas/1/preview?creator_id=1")
            assert resp.status_code == 403
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_delete_persona_same_creator_allowed():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"creator_id": 1})
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool), \
         patch("chatbotv2.dashboard.routes.personas.delete_persona", new_callable=AsyncMock, return_value=None), \
         patch("chatbotv2.dashboard.routes.personas.invalidate_persona_cache", new_callable=AsyncMock, return_value=None):
        client = TestClient(app)
        resp = client.delete("/api/personas/1?creator_id=1")
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_delete_persona_cross_creator_forbidden():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"creator_id": 2})
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool), \
         patch("db.postgres.delete_persona", new_callable=AsyncMock), \
         patch("db.redis.invalidate_persona_cache", new_callable=AsyncMock):
        client = TestClient(app)
        resp = client.delete("/api/personas/1?creator_id=1")
        assert resp.status_code == 403, resp.text
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_delete_requires_creator_id_when_owned():
    from fastapi.testclient import TestClient
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    async def fake_auth():
        return {"username": "tester"}

    app.dependency_overrides[require_auth] = fake_auth
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    # First call for _verify (creator_id None -> allow), second for our extra guard
    mock_conn.fetchrow = AsyncMock(return_value={"creator_id": 2})
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool), \
         patch("db.postgres.delete_persona", new_callable=AsyncMock), \
         patch("db.redis.invalidate_persona_cache", new_callable=AsyncMock):
        client = TestClient(app)
        # No creator_id param, persona has creator 2 -> should 403 (our extra guard)
        resp = client.delete("/api/personas/1")
        assert resp.status_code == 403
    app.dependency_overrides.clear()

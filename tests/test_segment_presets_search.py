"""Tests for Phase 6 presets and segment-aware search.

Covers: preset validation, preset API, preset activation, search integration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helper utilities ───────────────────────────────────────────────────


class _FakePoolConnCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _make_pool(mock_conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))
    return pool


# ── Preset Definition Tests ────────────────────────────────────────────


class TestPresetDefinitions:
    def test_all_presets_exist(self):
        from segments.presets import PRESETS

        preset_ids = [p.id for p in PRESETS]
        expected = [
            "recently_active", "inactive_fans", "high_message_count",
            "vip", "high_spenders", "repeat_buyers", "purchased_fans", "never_purchased",
            "new_fans", "at_risk_buyers",
            "pending_attention", "vault_engaged",
        ]
        for pid in expected:
            assert pid in preset_ids, f"Missing preset: {pid}"

    def test_preset_count(self):
        from segments.presets import PRESETS

        assert len(PRESETS) == 12

    def test_all_presets_validate(self):
        from segments.presets import PRESETS

        for preset in PRESETS:
            ok, err = preset.validate()
            assert ok is True, f"Preset '{preset.id}' validation failed: {err}"

    def test_all_presets_compile(self):
        from segments.evaluator import compile_rule
        from segments.presets import PRESETS

        for preset in PRESETS:
            sql, params = compile_rule(1, preset.rules)
            assert sql, f"Preset '{preset.id}' compiled to empty SQL"

    def test_preset_categories(self):
        from segments.presets import PRESETS

        categories = {p.category for p in PRESETS}
        assert "engagement" in categories
        assert "purchase" in categories
        assert "lifecycle" in categories
        assert "operations" in categories

    def test_preset_to_dict(self):
        from segments.presets import PRESETS

        for preset in PRESETS:
            d = preset.to_dict()
            assert "id" in d
            assert "name" in d
            assert "description" in d
            assert "category" in d
            assert "rules" in d
            assert d["id"] == preset.id

    def test_get_preset_found(self):
        from segments.presets import get_preset

        preset = get_preset("vip")
        assert preset is not None
        assert preset.name == "VIP Fans"

    def test_get_preset_not_found(self):
        from segments.presets import get_preset

        preset = get_preset("nonexistent")
        assert preset is None

    def test_list_presets(self):
        from segments.presets import list_presets

        presets = list_presets()
        assert isinstance(presets, list)
        assert len(presets) == 12
        for p in presets:
            assert "id" in p
            assert "name" in p


# ── Preset API Tests ───────────────────────────────────────────────────


class TestPresetAPI:
    @pytest.mark.asyncio
    async def test_list_presets(self, test_client):
        resp = await test_client.get("/api/segments/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 12

    @pytest.mark.asyncio
    async def test_activate_preset_ok(self, test_client):
        segment = {
            "id": 1, "creator_id": 1, "name": "VIP Fans", "description": "Fans at the VIP funnel stage",
            "rules": {"type": "group", "operator": "AND", "children": [
                {"type": "rule", "field": "funnel_stage", "operator": "=", "value": "vip"}
            ]},
            "enabled": True, "member_count": 5, "last_evaluated_at": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        }
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.create_segment", new_callable=AsyncMock, return_value=segment):
                with patch("chatbotv2.dashboard.routes.segments.get_segment_count", new_callable=AsyncMock, return_value=5):
                    with patch("chatbotv2.dashboard.routes.segments.sdb.update_member_count", new_callable=AsyncMock):
                        with patch("chatbotv2.dashboard.routes.segments.sdb.update_last_evaluated", new_callable=AsyncMock):
                            resp = await test_client.post("/api/segments/presets/vip/activate")
                            assert resp.status_code == 201
                            assert resp.json()["name"] == "VIP Fans"

    @pytest.mark.asyncio
    async def test_activate_preset_not_found(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            resp = await test_client.post("/api/segments/presets/nonexistent/activate")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_activate_preset_creator_isolation(self, test_client):
        segment = {
            "id": 1, "creator_id": 1, "name": "VIP Fans", "description": "",
            "rules": {"type": "group", "operator": "AND", "children": [
                {"type": "rule", "field": "funnel_stage", "operator": "=", "value": "vip"}
            ]},
            "enabled": True, "member_count": 5, "last_evaluated_at": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        }
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=42):
            with patch("chatbotv2.dashboard.routes.segments.sdb.create_segment", new_callable=AsyncMock, return_value=segment) as mock_create:
                with patch("chatbotv2.dashboard.routes.segments.get_segment_count", new_callable=AsyncMock, return_value=5):
                    with patch("chatbotv2.dashboard.routes.segments.sdb.update_member_count", new_callable=AsyncMock):
                        with patch("chatbotv2.dashboard.routes.segments.sdb.update_last_evaluated", new_callable=AsyncMock):
                            resp = await test_client.post("/api/segments/presets/vip/activate")
                            assert resp.status_code == 201
                            # Verify creator_id was passed correctly
                            mock_create.assert_called_once()
                            call_args = mock_create.call_args
                            assert call_args[0][0] == 42  # creator_id


# ── Search Integration Tests ───────────────────────────────────────────


class TestSearchIntegration:
    @pytest.mark.asyncio
    async def test_search_without_segment_preserves_behavior(self, test_client):
        with patch("chatbotv2.dashboard.routes.search.search_messages", new_callable=AsyncMock, return_value={"items": [], "pagination": {}}):
            with patch("chatbotv2.dashboard.routes.search.search_conversation_notes", new_callable=AsyncMock, return_value={"items": [], "pagination": {}}):
                with patch("chatbotv2.dashboard.routes.search.search_users", new_callable=AsyncMock, return_value={"items": [], "pagination": {}}):
                    resp = await test_client.get("/api/search?q=test")
                    assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_with_segment_id(self, test_client):
        segment = {
            "id": 1, "creator_id": 1, "name": "Test", "description": "",
            "rules": {"type": "group", "operator": "AND", "children": [
                {"type": "rule", "field": "message_count", "operator": ">=", "value": 10}
            ]},
            "enabled": True,
        }
        with patch("chatbotv2.dashboard.routes.search.resolve_single_application_creator", new_callable=AsyncMock) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.status = "ready"
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            with patch("chatbotv2.dashboard.routes.search.sdb.get_segment", new_callable=AsyncMock, return_value=segment):
                with patch("chatbotv2.dashboard.routes.search.compile_rule", return_value=("u.message_count >= $2", [1, 10])):
                    mock_conn = AsyncMock()
                    mock_conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
                    pool = _make_pool(mock_conn)
                    with patch("db.postgres.get_pool", return_value=pool):
                        with patch("chatbotv2.dashboard.routes.search.search_users", new_callable=AsyncMock, return_value={"items": [{"id": 1, "similarity": 0.9}], "pagination": {}}):
                            resp = await test_client.get("/api/search?q=test&segment_id=1&scope=users")
                            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_with_nonexistent_segment(self, test_client):
        with patch("chatbotv2.dashboard.routes.search.resolve_single_application_creator", new_callable=AsyncMock) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.status = "ready"
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            with patch("chatbotv2.dashboard.routes.search.sdb.get_segment", new_callable=AsyncMock, return_value=None):
                resp = await test_client.get("/api/search?q=test&segment_id=999")
                assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_search_with_disabled_segment(self, test_client):
        segment = {"id": 1, "creator_id": 1, "name": "Test", "enabled": False}
        with patch("chatbotv2.dashboard.routes.search.resolve_single_application_creator", new_callable=AsyncMock) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.status = "ready"
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            with patch("chatbotv2.dashboard.routes.search.sdb.get_segment", new_callable=AsyncMock, return_value=segment):
                resp = await test_client.get("/api/search?q=test&segment_id=1")
                assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_search_with_empty_segment_results(self, test_client):
        segment = {
            "id": 1, "creator_id": 1, "name": "Test", "description": "",
            "rules": {"type": "group", "operator": "AND", "children": [
                {"type": "rule", "field": "message_count", "operator": ">=", "value": 999999}
            ]},
            "enabled": True,
        }
        with patch("chatbotv2.dashboard.routes.search.resolve_single_application_creator", new_callable=AsyncMock) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.status = "ready"
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            with patch("chatbotv2.dashboard.routes.search.sdb.get_segment", new_callable=AsyncMock, return_value=segment):
                with patch("chatbotv2.dashboard.routes.search.compile_rule", return_value=("u.message_count >= $2", [1, 999999])):
                    mock_conn = AsyncMock()
                    mock_conn.fetch = AsyncMock(return_value=[])
                    pool = _make_pool(mock_conn)
                    with patch("db.postgres.get_pool", return_value=pool):
                        resp = await test_client.get("/api/search?q=test&segment_id=1")
                        assert resp.status_code == 200
                        assert resp.json()["items"] == []

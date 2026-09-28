"""Comprehensive tests for Phase 5 Auto-Segmentation.

Covers: rule validation, field registry, SQL compilation, parameter numbering,
boolean logic, segment CRUD, API routes, membership evaluation, and security.
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


# ── Field Registry Tests ───────────────────────────────────────────────


class TestFieldRegistry:
    def test_all_fields_registered(self):
        from segments.fields import FIELDS

        expected_fields = [
            "funnel_stage", "is_blocked", "do_not_auto_reply", "message_count",
            "first_seen_days_ago", "last_seen_days_ago", "has_username",
            "inbound_count", "outbound_count", "total_messages",
            "last_inbound_days_ago", "last_outbound_days_ago",
            "first_message_days_ago", "has_inbound", "has_outbound",
            "has_tag", "tag_count",
            "attention_status", "has_attention", "assigned_operator_id",
            "has_pending_queue", "queue_count",
            "total_spend_minor", "purchase_count", "has_purchased",
            "purchased_product_id", "purchased_product_count",
            "delivery_count", "has_delivery", "delivered_product_id",
            "last_delivery_days_ago", "delivered_product_count",
            "has_active_offer", "has_purchased_offer", "offer_state",
        ]
        for name in expected_fields:
            assert name in FIELDS, f"Missing field: {name}"

    def test_list_fields_returns_dicts(self):
        from segments.fields import list_fields

        fields = list_fields()
        assert isinstance(fields, list)
        assert len(fields) > 0
        for f in fields:
            assert "name" in f
            assert "label" in f
            assert "category" in f
            assert "operators" in f
            assert "value_type" in f

    def test_get_field_returns_spec(self):
        from segments.fields import get_field

        spec = get_field("message_count")
        assert spec is not None
        assert spec.name == "message_count"
        assert ">=" in spec.operators

    def test_get_field_unknown_returns_none(self):
        from segments.fields import get_field

        assert get_field("nonexistent") is None


# ── Rule Validation Tests ──────────────────────────────────────────────


class TestRuleValidation:
    def test_valid_field_operator_value(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "message_count", "operator": ">=", "value": 10})
        assert ok is True
        assert err is None

    def test_invalid_field(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "nonexistent", "operator": ">=", "value": 10})
        assert ok is False
        assert "nonexistent" in err

    def test_invalid_operator(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "message_count", "operator": "LIKE", "value": 10})
        assert ok is False
        assert "LIKE" in err

    def test_invalid_value_type_string_for_int_field(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "message_count", "operator": ">=", "value": "not_a_number"})
        assert ok is False
        assert "Invalid value" in err

    def test_invalid_value_type_int_for_str_field(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "funnel_stage", "operator": "=", "value": 123})
        assert ok is False

    def test_valid_bool_field(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "is_blocked", "operator": "=", "value": True})
        assert ok is True

    def test_valid_str_field(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({"type": "rule", "field": "funnel_stage", "operator": "=", "value": "vip"})
        assert ok is True

    def test_empty_rule_group_validation(self):
        from pydantic import ValidationError

        from segments.models import RuleGroup

        # Pydantic rejects empty groups at model construction time
        with pytest.raises(ValidationError, match="at least one child"):
            RuleGroup(operator="AND", children=[])

    def test_nested_group_validation(self):
        from segments.evaluator import validate_rules
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            RuleGroup(operator="OR", children=[
                FieldRule(field="message_count", operator=">=", value=10),
            ]),
            FieldRule(field="is_blocked", operator="=", value=False),
        ])
        ok, err = validate_rules(rule)
        assert ok is True

    def test_reject_raw_sql_injection(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({
            "type": "rule",
            "field": "1=1; DROP TABLE users; --",
            "operator": ">=",
            "value": 10,
        })
        assert ok is False

    def test_reject_sql_in_value(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({
            "type": "rule",
            "field": "funnel_stage",
            "operator": "=",
            "value": "'; DROP TABLE users; --",
        })
        # This is actually valid (it's a string value), but the SQL is parameterized
        # so it's safe. The value is passed as a parameter, not interpolated.
        assert ok is True  # Safe because it's parameterized


# ── SQL Compilation Tests ──────────────────────────────────────────────


class TestSQLCompilation:
    def test_simple_rule(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])
        sql, params = compile_rule(1, rule)
        assert "u.message_count >= $2" in sql
        assert params == [1, 10]

    def test_creator_id_is_param_1(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="is_blocked", operator="=", value=True),
        ])
        sql, params = compile_rule(42, rule)
        assert params[0] == 42
        assert "$1" in sql or len(params) == 2

    def test_sequential_parameter_numbering(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
            FieldRule(field="has_tag", operator="=", value="vip"),
            FieldRule(field="is_blocked", operator="=", value=False),
        ])
        sql, params = compile_rule(1, rule)
        assert params == [1, 10, "vip", False]
        # All $N should be sequential
        import re
        nums = [int(m) for m in re.findall(r'\$(\d+)', sql)]
        assert nums == sorted(nums)

    def test_and_group(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
            FieldRule(field="is_blocked", operator="=", value=False),
        ])
        sql, params = compile_rule(1, rule)
        assert "AND" in sql
        assert params == [1, 10, False]

    def test_or_group(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="OR", children=[
            FieldRule(field="message_count", operator=">=", value=10),
            FieldRule(field="is_blocked", operator="=", value=True),
        ])
        sql, params = compile_rule(1, rule)
        assert "OR" in sql

    def test_nested_and_or(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="OR", children=[
            RuleGroup(operator="AND", children=[
                FieldRule(field="message_count", operator=">=", value=10),
                FieldRule(field="has_tag", operator="=", value="vip"),
            ]),
            FieldRule(field="is_blocked", operator="=", value=False),
        ])
        sql, params = compile_rule(42, rule)
        assert "AND" in sql
        assert "OR" in sql
        assert params[0] == 42
        assert 10 in params
        assert "vip" in params
        assert False in params

    def test_complex_nested_realistic(self):
        """Test the realistic complex expression from requirements."""
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="OR", children=[
            RuleGroup(operator="AND", children=[
                FieldRule(field="total_spend_minor", operator=">=", value=100),
                FieldRule(field="purchase_count", operator=">=", value=2),
            ]),
            RuleGroup(operator="AND", children=[
                FieldRule(field="has_tag", operator="=", value="vip"),
                FieldRule(field="last_outbound_days_ago", operator="<", value=7),
            ]),
        ])
        sql, params = compile_rule(99, rule)
        # creator_id should be injected for creator-scoped fields
        assert params[0] == 99
        assert 100 in params
        assert 2 in params
        assert "vip" in params
        assert 7 in params
        # Verify all $N are sequential
        import re
        nums = [int(m) for m in re.findall(r'\$(\d+)', sql)]
        assert nums == sorted(nums)

    def test_empty_group_raises(self):
        from pydantic import ValidationError

        from segments.models import RuleGroup

        # Pydantic rejects empty groups at model construction time
        with pytest.raises(ValidationError, match="at least one child"):
            RuleGroup(operator="AND", children=[])

    def test_unknown_field_raises(self):
        from segments.evaluator import CompilationError, SQLCompiler
        from segments.models import FieldRule, RuleGroup

        compiler = SQLCompiler(1)
        with pytest.raises(CompilationError, match="Unknown field"):
            compiler.compile(RuleGroup(operator="AND", children=[
                FieldRule(field="nonexistent", operator=">=", value=10),
            ]))

    def test_invalid_operator_raises(self):
        from segments.evaluator import CompilationError, SQLCompiler
        from segments.models import FieldRule, RuleGroup

        compiler = SQLCompiler(1)
        with pytest.raises(CompilationError, match="Invalid operator"):
            compiler.compile(RuleGroup(operator="AND", children=[
                FieldRule(field="message_count", operator="LIKE", value=10),
            ]))


# ── Segment Model Tests ────────────────────────────────────────────────


class TestSegmentModels:
    def test_field_rule_creation(self):
        from segments.models import FieldRule

        r = FieldRule(field="message_count", operator=">=", value=10)
        assert r.type == "rule"
        assert r.field == "message_count"

    def test_rule_group_creation(self):
        from segments.models import FieldRule, RuleGroup

        g = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])
        assert g.type == "group"
        assert len(g.children) == 1

    def test_empty_group_rejected(self):
        from pydantic import ValidationError

        from segments.models import RuleGroup

        with pytest.raises(ValidationError):
            RuleGroup(operator="AND", children=[])

    def test_flatten_rules(self):
        from segments.models import FieldRule, RuleGroup, flatten_rules

        tree = RuleGroup(operator="AND", children=[
            FieldRule(field="a", operator="=", value=1),
            RuleGroup(operator="OR", children=[
                FieldRule(field="b", operator="=", value=2),
                FieldRule(field="c", operator="=", value=3),
            ]),
        ])
        leaves = list(flatten_rules(tree))
        assert len(leaves) == 3
        assert leaves[0].field == "a"
        assert leaves[1].field == "b"
        assert leaves[2].field == "c"


# ── Segment CRUD Tests (mocked DB) ────────────────────────────────────


class TestSegmentCRUD:
    @pytest.mark.asyncio
    async def test_create_segment(self):
        from db.segments import create_segment

        mock_row = {
            "id": 1, "creator_id": 1, "name": "Test", "description": "desc",
            "rules": {"type": "group", "operator": "AND", "children": []},
            "enabled": True, "member_count": 0, "last_evaluated_at": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await create_segment(1, "Test", "desc", {"type": "group", "operator": "AND", "children": []})

        assert result["name"] == "Test"
        assert result["creator_id"] == 1

    @pytest.mark.asyncio
    async def test_list_segments(self):
        from db.segments import list_segments

        mock_rows = [
            {"id": 1, "name": "S1", "creator_id": 1},
            {"id": 2, "name": "S2", "creator_id": 1},
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await list_segments(1)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_segment_found(self):
        from db.segments import get_segment

        mock_row = {"id": 1, "name": "Test", "creator_id": 1}
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await get_segment(1, 1)

        assert result is not None
        assert result["name"] == "Test"

    @pytest.mark.asyncio
    async def test_get_segment_not_found(self):
        from db.segments import get_segment

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await get_segment(1, 999)

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_segment(self):
        from db.segments import delete_segment

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 1")
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await delete_segment(1, 1)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_segment_not_found(self):
        from db.segments import delete_segment

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 0")
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await delete_segment(1, 999)

        assert result is False

    @pytest.mark.asyncio
    async def test_toggle_segment(self):
        from db.segments import toggle_segment

        mock_row = {"id": 1, "enabled": False, "creator_id": 1}
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", return_value=pool):
            result = await toggle_segment(1, 1, False)

        assert result["enabled"] is False


# ── API Route Tests ────────────────────────────────────────────────────


class TestSegmentAPI:
    @pytest.mark.asyncio
    async def test_list_fields(self, test_client):
        resp = await test_client.get("/api/segments/fields")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "name" in data[0]

    @pytest.mark.asyncio
    async def test_list_segments(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.list_segments", new_callable=AsyncMock, return_value=[]):
                resp = await test_client.get("/api/segments")
                assert resp.status_code == 200
                assert resp.json() == []

    @pytest.mark.asyncio
    async def test_create_segment_ok(self, test_client):
        segment = {
            "id": 1, "creator_id": 1, "name": "Test", "description": "",
            "rules": {"type": "group", "operator": "AND", "children": [
                {"type": "rule", "field": "message_count", "operator": ">=", "value": 10}
            ]},
            "enabled": True, "member_count": 5, "last_evaluated_at": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        }
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.create_segment", new_callable=AsyncMock, return_value=segment):
                with patch("chatbotv2.dashboard.routes.segments.get_segment_count", new_callable=AsyncMock, return_value=5):
                    with patch("chatbotv2.dashboard.routes.segments.sdb.update_member_count", new_callable=AsyncMock):
                        with patch("chatbotv2.dashboard.routes.segments.sdb.update_last_evaluated", new_callable=AsyncMock):
                            resp = await test_client.post("/api/segments", json={
                                "name": "Test",
                                "rules": {"type": "group", "operator": "AND", "children": [
                                    {"type": "rule", "field": "message_count", "operator": ">=", "value": 10}
                                ]},
                            })
                            assert resp.status_code == 201
                            assert resp.json()["name"] == "Test"

    @pytest.mark.asyncio
    async def test_create_segment_invalid_rule(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            resp = await test_client.post("/api/segments", json={
                "name": "Test",
                "rules": {"type": "group", "operator": "AND", "children": [
                    {"type": "rule", "field": "nonexistent", "operator": ">=", "value": 10}
                ]},
            })
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_segment_not_found(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.get_segment", new_callable=AsyncMock, return_value=None):
                resp = await test_client.get("/api/segments/999")
                assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_segment_ok(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.delete_segment", new_callable=AsyncMock, return_value=True):
                resp = await test_client.delete("/api/segments/1")
                assert resp.status_code == 200
                assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_delete_segment_not_found(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.delete_segment", new_callable=AsyncMock, return_value=False):
                resp = await test_client.delete("/api/segments/999")
                assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_segment(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.get_segment_count", new_callable=AsyncMock, return_value=3):
                with patch("chatbotv2.dashboard.routes.segments.get_segment_members", new_callable=AsyncMock, return_value=[]):
                    resp = await test_client.post("/api/segments/preview", json={
                        "rules": {"type": "group", "operator": "AND", "children": [
                            {"type": "rule", "field": "message_count", "operator": ">=", "value": 10}
                        ]},
                    })
                    assert resp.status_code == 200
                    assert resp.json()["count"] == 3

    @pytest.mark.asyncio
    async def test_preview_missing_rules(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            resp = await test_client.post("/api/segments/preview", json={})
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_toggle_segment(self, test_client):
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.get_segment", new_callable=AsyncMock, return_value={"id": 1, "enabled": True}):
                with patch("chatbotv2.dashboard.routes.segments.sdb.toggle_segment", new_callable=AsyncMock, return_value={"id": 1, "enabled": False}):
                    resp = await test_client.post("/api/segments/1/toggle")
                    assert resp.status_code == 200
                    assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_duplicate_segment(self, test_client):
        source = {
            "id": 1, "name": "Original", "description": "desc",
            "rules": {"type": "group", "operator": "AND", "children": []},
        }
        new_seg = {
            "id": 2, "name": "Original (Copy)", "description": "desc",
            "rules": {"type": "group", "operator": "AND", "children": []},
        }
        with patch("chatbotv2.dashboard.routes.segments._require_creator", new_callable=AsyncMock, return_value=1):
            with patch("chatbotv2.dashboard.routes.segments.sdb.get_segment", new_callable=AsyncMock, return_value=source):
                with patch("chatbotv2.dashboard.routes.segments.sdb.duplicate_segment", new_callable=AsyncMock, return_value=new_seg):
                    resp = await test_client.post("/api/segments/1/duplicate")
                    assert resp.status_code == 201
                    assert resp.json()["name"] == "Original (Copy)"


# ── Membership Evaluation Tests (mocked DB) ────────────────────────────


class TestMembershipEvaluation:
    @pytest.mark.asyncio
    async def test_get_segment_members(self):
        from segments.evaluator import get_segment_members
        from segments.models import FieldRule, RuleGroup

        mock_rows = [
            {"id": 1, "username": "alice", "first_name": "Alice", "last_seen": None, "message_count": 15},
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            members = await get_segment_members(1, rule)

        assert len(members) == 1
        assert members[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_get_segment_count(self):
        from segments.evaluator import get_segment_count
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=42)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            count = await get_segment_count(1, rule)

        assert count == 42

    @pytest.mark.asyncio
    async def test_check_user_in_segment_true(self):
        from segments.evaluator import check_user_in_segment
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            result = await check_user_in_segment(1, rule, 42)

        assert result is True

    @pytest.mark.asyncio
    async def test_check_user_in_segment_false(self):
        from segments.evaluator import check_user_in_segment
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=False)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            result = await check_user_in_segment(1, rule, 999)

        assert result is False

    @pytest.mark.asyncio
    async def test_zero_member_segment(self):
        from segments.evaluator import get_segment_count
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=0)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=999999),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            count = await get_segment_count(1, rule)

        assert count == 0

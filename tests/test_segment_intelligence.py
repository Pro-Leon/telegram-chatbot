"""Tests for Phase 5.4 — Segment Intelligence, Explainability & Lifecycle Hardening.

Covers: explanation engine, API endpoints, batch segments, list_segments enabled_only,
bulk_ops return order fix, segment statistics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────


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


FAKE_SEGMENT = {
    "id": 1,
    "creator_id": 100,
    "name": "Active Buyers",
    "description": "Fans who have purchased",
    "rules": {
        "type": "group",
        "operator": "AND",
        "children": [
            {"type": "rule", "field": "is_blocked", "operator": "=", "value": False},
            {"type": "rule", "field": "has_purchased", "operator": "=", "value": True},
        ],
    },
    "enabled": True,
    "member_count": 5,
    "last_evaluated_at": None,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

FAKE_SEGMENT_SINGLE_RULE = {
    **FAKE_SEGMENT,
    "id": 2,
    "name": "High Message Count",
    "rules": {
        "type": "group",
        "operator": "AND",
        "children": [
            {"type": "rule", "field": "message_count", "operator": ">=", "value": 100},
        ],
    },
}


def _make_creator_ctx(ready=True):
    from commerce.single_creator import SingleCreatorStatus
    ctx = MagicMock()
    ctx.status = SingleCreatorStatus.READY if ready else SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
    ctx.creator_id = 100 if ready else None
    return ctx


# ── Explanation Engine Tests ─────────────────────────────────────────────


class TestExplanationModels:
    def test_field_evaluation_model(self):
        from segments.models import FieldEvaluation
        ev = FieldEvaluation(
            field="message_count",
            field_label="Message Count",
            operator=">=",
            value=100,
            satisfied=True,
            actual_value=150,
        )
        assert ev.field == "message_count"
        assert ev.satisfied is True
        assert ev.actual_value == 150

    def test_rule_evaluation_model(self):
        from segments.models import FieldEvaluation, RuleEvaluation
        ev = RuleEvaluation(
            operator="AND",
            children=[
                FieldEvaluation(
                    field="is_blocked", field_label="Blocked",
                    operator="=", value=False, satisfied=True,
                ),
            ],
            satisfied=True,
        )
        assert ev.operator == "AND"
        assert ev.satisfied is True
        assert len(ev.children) == 1

    def test_segment_explanation_model(self):
        from segments.models import FieldEvaluation, RuleEvaluation, SegmentExplanation
        exp = SegmentExplanation(
            user_id=123,
            segment_id=1,
            segment_name="Test",
            is_member=True,
            rule_evaluation=RuleEvaluation(operator="AND", children=[], satisfied=True),
            satisfied_count=2,
            total_count=2,
        )
        assert exp.user_id == 123
        assert exp.is_member is True
        assert exp.satisfied_count == 2


class TestExplainRule:
    @pytest.mark.asyncio
    async def test_explain_single_satisfied(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(
            operator="AND",
            children=[FieldRule(field="message_count", operator=">=", value=50)],
        )

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        with patch("segments.evaluator.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await explain_rule(100, rule, 123)

        assert result.satisfied is True
        assert len(result.children) == 1
        assert result.children[0].satisfied is True

    @pytest.mark.asyncio
    async def test_explain_single_not_satisfied(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(
            operator="AND",
            children=[FieldRule(field="message_count", operator=">=", value=50)],
        )

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=False)
        pool = _make_pool(mock_conn)

        with patch("segments.evaluator.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await explain_rule(100, rule, 123)

        assert result.satisfied is False

    @pytest.mark.asyncio
    async def test_explain_and_group_all_satisfied(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(
            operator="AND",
            children=[
                FieldRule(field="is_blocked", operator="=", value=False),
                FieldRule(field="has_purchased", operator="=", value=True),
            ],
        )

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        with patch("segments.evaluator.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await explain_rule(100, rule, 123)

        assert result.satisfied is True
        assert result.operator == "AND"
        assert all(c.satisfied for c in result.children)

    @pytest.mark.asyncio
    async def test_explain_and_group_partial(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(
            operator="AND",
            children=[
                FieldRule(field="is_blocked", operator="=", value=False),
                FieldRule(field="has_purchased", operator="=", value=True),
            ],
        )

        call_count = 0
        async def mock_fetchval(query, *args):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # first True, second False

        mock_conn = AsyncMock()
        mock_conn.fetchval = mock_fetchval
        pool = _make_pool(mock_conn)

        with patch("segments.evaluator.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await explain_rule(100, rule, 123)

        assert result.satisfied is False
        assert result.children[0].satisfied is True
        assert result.children[1].satisfied is False

    @pytest.mark.asyncio
    async def test_explain_or_group_one_satisfied(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(
            operator="OR",
            children=[
                FieldRule(field="is_blocked", operator="=", value=False),
                FieldRule(field="has_purchased", operator="=", value=True),
            ],
        )

        call_count = 0
        async def mock_fetchval(query, *args):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # first True, second False

        mock_conn = AsyncMock()
        mock_conn.fetchval = mock_fetchval
        pool = _make_pool(mock_conn)

        with patch("segments.evaluator.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await explain_rule(100, rule, 123)

        assert result.satisfied is True
        assert result.operator == "OR"

    @pytest.mark.asyncio
    async def test_explain_nested_groups(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(
            operator="AND",
            children=[
                FieldRule(field="is_blocked", operator="=", value=False),
                RuleGroup(
                    operator="OR",
                    children=[
                        FieldRule(field="has_purchased", operator="=", value=True),
                        FieldRule(field="has_delivery", operator="=", value=True),
                    ],
                ),
            ],
        )

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        with patch("segments.evaluator.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await explain_rule(100, rule, 123)

        assert result.satisfied is True
        assert len(result.children) == 2
        assert result.children[1].operator == "OR"


class TestCountEvaluationLeaves:
    def test_single_satisfied(self):
        from segments.evaluator import _count_evaluation_leaves
        from segments.models import FieldEvaluation
        ev = FieldEvaluation(
            field="x", field_label="X", operator="=", value=1, satisfied=True,
        )
        sat, total = _count_evaluation_leaves(ev)
        assert sat == 1
        assert total == 1

    def test_single_not_satisfied(self):
        from segments.evaluator import _count_evaluation_leaves
        from segments.models import FieldEvaluation
        ev = FieldEvaluation(
            field="x", field_label="X", operator="=", value=1, satisfied=False,
        )
        sat, total = _count_evaluation_leaves(ev)
        assert sat == 0
        assert total == 1

    def test_group_mixed(self):
        from segments.evaluator import _count_evaluation_leaves
        from segments.models import FieldEvaluation, RuleEvaluation
        ev = RuleEvaluation(
            operator="AND",
            children=[
                FieldEvaluation(field="a", field_label="A", operator="=", value=1, satisfied=True),
                FieldEvaluation(field="b", field_label="B", operator="=", value=2, satisfied=False),
                FieldEvaluation(field="c", field_label="C", operator="=", value=3, satisfied=True),
            ],
        )
        sat, total = _count_evaluation_leaves(ev)
        assert sat == 2
        assert total == 3


# ── list_segments enabled_only ──────────────────────────────────────────


class TestListSegmentsEnabledOnly:
    @pytest.mark.asyncio
    async def test_enabled_only_filters_disabled(self):
        from db.segments import list_segments
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "name": "Active", "enabled": True},
        ])
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await list_segments(100, enabled_only=True)

        assert len(result) == 1
        call_args = mock_conn.fetch.call_args
        query = call_args[0][0]
        assert "enabled = TRUE" in query

    @pytest.mark.asyncio
    async def test_enabled_only_false_returns_all(self):
        from db.segments import list_segments
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "name": "Active", "enabled": True},
            {"id": 2, "name": "Disabled", "enabled": False},
        ])
        pool = _make_pool(mock_conn)

        with patch("db.segments.get_pool", new_callable=AsyncMock, return_value=pool):
            result = await list_segments(100, enabled_only=False)

        assert len(result) == 2
        call_args = mock_conn.fetch.call_args
        query = call_args[0][0]
        assert "enabled = TRUE" not in query


# ── Bulk ops return order fix ───────────────────────────────────────────


class TestBulkOpsResolveUserIds:
    @pytest.mark.asyncio
    async def test_resolve_returns_tuple_with_error_second(self):
        from chatbotv2.dashboard.routes.bulk_ops import _resolve_user_ids

        # Empty list should return ([], JSONResponse)
        user_ids, err = await _resolve_user_ids([], None)
        assert user_ids == []
        assert err is not None
        assert err.status_code == 400

    @pytest.mark.asyncio
    async def test_resolve_returns_ids_on_success(self):
        from chatbotv2.dashboard.routes.bulk_ops import _resolve_user_ids

        user_ids, err = await _resolve_user_ids([1, 2, 3], None)
        assert user_ids == [1, 2, 3]
        assert err is None

    @pytest.mark.asyncio
    async def test_resolve_segment_merges_ids(self):
        from chatbotv2.dashboard.routes.bulk_ops import _resolve_user_ids

        fake_members = [{"id": 10}, {"id": 20}]
        with patch(
            "chatbotv2.dashboard.routes.bulk_ops.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(),
        ), patch(
            "chatbotv2.dashboard.routes.bulk_ops.sdb.get_segment",
            new_callable=AsyncMock,
            return_value=FAKE_SEGMENT,
        ), patch(
            "chatbotv2.dashboard.routes.bulk_ops.get_segment_members",
            new_callable=AsyncMock,
            return_value=fake_members,
        ):
            user_ids, err = await _resolve_user_ids([1, 2], segment_id=1)

        assert err is None
        assert set(user_ids) == {1, 2, 10, 20}

    @pytest.mark.asyncio
    async def test_resolve_segment_not_found(self):
        from chatbotv2.dashboard.routes.bulk_ops import _resolve_user_ids

        with patch(
            "chatbotv2.dashboard.routes.bulk_ops.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(),
        ), patch(
            "chatbotv2.dashboard.routes.bulk_ops.sdb.get_segment",
            new_callable=AsyncMock,
            return_value=None,
        ):
            user_ids, err = await _resolve_user_ids([1], segment_id=999)

        assert user_ids == []
        assert err is not None
        assert err.status_code == 404

    @pytest.mark.asyncio
    async def test_resolve_segment_disabled(self):
        from chatbotv2.dashboard.routes.bulk_ops import _resolve_user_ids

        disabled_seg = {**FAKE_SEGMENT, "enabled": False}
        with patch(
            "chatbotv2.dashboard.routes.bulk_ops.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(),
        ), patch(
            "chatbotv2.dashboard.routes.bulk_ops.sdb.get_segment",
            new_callable=AsyncMock,
            return_value=disabled_seg,
        ):
            user_ids, err = await _resolve_user_ids([1], segment_id=1)

        assert user_ids == []
        assert err is not None
        assert err.status_code == 400


# ── API Endpoint Tests ───────────────────────────────────────────────────


class TestExplainEndpoint:
    @pytest.mark.asyncio
    async def test_explain_user_segments_endpoint(self):
        from chatbotv2.dashboard.routes.users import api_user_segments_explain

        fake_explain = [
            {
                "segment_id": 1,
                "segment_name": "Test",
                "is_member": True,
                "satisfied_count": 2,
                "total_count": 2,
                "rule_evaluation": {"type": "group", "operator": "AND", "children": [], "satisfied": True},
            }
        ]
        with patch(
            "chatbotv2.dashboard.routes.users.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(),
        ), patch(
            "chatbotv2.dashboard.routes.users.explain_user_segments",
            new_callable=AsyncMock,
            return_value=fake_explain,
        ):
            resp = await api_user_segments_explain(123, auth={"username": "test"})

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body["user_id"] == 123
        assert len(body["segments"]) == 1
        assert body["segments"][0]["is_member"] is True

    @pytest.mark.asyncio
    async def test_explain_endpoint_no_creator(self):
        from chatbotv2.dashboard.routes.users import api_user_segments_explain

        with patch(
            "chatbotv2.dashboard.routes.users.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(ready=False),
        ):
            resp = await api_user_segments_explain(123, auth={"username": "test"})

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body["segments"] == []


class TestBatchSegmentsEndpoint:
    @pytest.mark.asyncio
    async def test_batch_segments_empty(self):
        from chatbotv2.dashboard.routes.users import api_users_segments_batch

        resp = await api_users_segments_batch(user_ids="", auth={"username": "test"})
        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body == {}

    @pytest.mark.asyncio
    async def test_batch_segments_no_creator(self):
        from chatbotv2.dashboard.routes.users import api_users_segments_batch

        with patch(
            "chatbotv2.dashboard.routes.users.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(ready=False),
        ):
            resp = await api_users_segments_batch(user_ids="1,2,3", auth={"username": "test"})

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body == {"1": [], "2": [], "3": []}

    @pytest.mark.asyncio
    async def test_batch_segments_with_members(self):
        from chatbotv2.dashboard.routes.users import api_users_segments_batch

        mock_conn = AsyncMock()
        call_count = 0
        async def mock_fetchval(query, *args):
            nonlocal call_count
            call_count += 1
            # user 1 is member, user 2 is not
            return args[-1] == 1 if call_count <= 2 else args[-1] == 1

        mock_conn.fetchval = mock_fetchval
        pool = _make_pool(mock_conn)

        with patch(
            "chatbotv2.dashboard.routes.users.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=_make_creator_ctx(),
        ), patch(
            "chatbotv2.dashboard.routes.users.sdb.list_segments",
            new_callable=AsyncMock,
            return_value=[FAKE_SEGMENT],
        ), patch(
            "chatbotv2.dashboard.routes.users.get_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            resp = await api_users_segments_batch(user_ids="1,2", auth={"username": "test"})

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert "1" in body
        assert "2" in body

    @pytest.mark.asyncio
    async def test_batch_segments_invalid_format(self):
        from chatbotv2.dashboard.routes.users import api_users_segments_batch

        resp = await api_users_segments_batch(user_ids="abc,def", auth={"username": "test"})
        assert resp.status_code == 400


class TestSegmentStatsEndpoint:
    @pytest.mark.asyncio
    async def test_stats_returns_field_level_data(self):
        from chatbotv2.dashboard.routes.segments import api_segment_stats

        fake_stats = {
            "segment_id": 1,
            "member_count": 42,
            "last_evaluated_at": None,
            "field_stats": [
                {"field": "message_count", "field_label": "Messages", "operator": ">=", "value": 100, "matching_count": 42},
            ],
        }
        with patch(
            "chatbotv2.dashboard.routes.segments._require_creator",
            new_callable=AsyncMock,
            return_value=100,
        ), patch(
            "chatbotv2.dashboard.routes.segments.get_segment_stats",
            new_callable=AsyncMock,
            return_value=fake_stats,
        ):
            resp = await api_segment_stats(segment_id=1, auth={"username": "test"})

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body["member_count"] == 42
        assert len(body["field_stats"]) == 1

    @pytest.mark.asyncio
    async def test_stats_segment_not_found(self):
        from chatbotv2.dashboard.routes.segments import api_segment_stats
        from fastapi import HTTPException

        with patch(
            "chatbotv2.dashboard.routes.segments._require_creator",
            new_callable=AsyncMock,
            return_value=100,
        ), patch(
            "chatbotv2.dashboard.routes.segments.get_segment_stats",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await api_segment_stats(segment_id=999, auth={"username": "test"})
            assert exc_info.value.status_code == 404


class TestExplainSegmentUserEndpoint:
    @pytest.mark.asyncio
    async def test_explain_segment_user(self):
        from chatbotv2.dashboard.routes.segments import api_explain_segment_user
        from segments.models import FieldEvaluation, RuleEvaluation

        fake_eval = RuleEvaluation(
            operator="AND",
            children=[
                FieldEvaluation(
                    field="message_count", field_label="Messages",
                    operator=">=", value=100, satisfied=True, actual_value=150,
                ),
            ],
            satisfied=True,
        )
        with patch(
            "chatbotv2.dashboard.routes.segments._require_creator",
            new_callable=AsyncMock,
            return_value=100,
        ), patch(
            "chatbotv2.dashboard.routes.segments.sdb.get_segment",
            new_callable=AsyncMock,
            return_value=FAKE_SEGMENT,
        ), patch(
            "chatbotv2.dashboard.routes.segments.explain_rule",
            new_callable=AsyncMock,
            return_value=fake_eval,
        ):
            resp = await api_explain_segment_user(segment_id=1, user_id=123, auth={"username": "test"})

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body["is_member"] is True
        assert body["satisfied_count"] == 1
        assert body["total_count"] == 1

    @pytest.mark.asyncio
    async def test_explain_segment_not_found(self):
        from chatbotv2.dashboard.routes.segments import api_explain_segment_user
        from fastapi import HTTPException

        with patch(
            "chatbotv2.dashboard.routes.segments._require_creator",
            new_callable=AsyncMock,
            return_value=100,
        ), patch(
            "chatbotv2.dashboard.routes.segments.sdb.get_segment",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await api_explain_segment_user(segment_id=999, user_id=123, auth={"username": "test"})
            assert exc_info.value.status_code == 404


# ── get_segment_stats ────────────────────────────────────────────────────


class TestGetSegmentStats:
    @pytest.mark.asyncio
    async def test_stats_returns_none_for_missing_segment(self):
        from segments.evaluator import get_segment_stats

        with patch(
            "db.segments.get_segment",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await get_segment_stats(100, 999)

        assert result is None

    @pytest.mark.asyncio
    async def test_stats_returns_field_stats(self):
        from segments.evaluator import get_segment_stats

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=42)
        pool = _make_pool(mock_conn)

        with patch(
            "db.segments.get_segment",
            new_callable=AsyncMock,
            return_value=FAKE_SEGMENT,
        ), patch(
            "segments.evaluator.get_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            result = await get_segment_stats(100, 1)

        assert result is not None
        assert result["segment_id"] == 1
        assert len(result["field_stats"]) == 2
        for fs in result["field_stats"]:
            assert "field" in fs
            assert "matching_count" in fs

    @pytest.mark.asyncio
    async def test_stats_empty_rules(self):
        from segments.evaluator import get_segment_stats

        seg_no_rules = {**FAKE_SEGMENT, "rules": None}
        with patch(
            "db.segments.get_segment",
            new_callable=AsyncMock,
            return_value=seg_no_rules,
        ):
            result = await get_segment_stats(100, 1)

        assert result is not None
        assert result["field_stats"] == []

"""Comprehensive tests for Phase 6 NOT/Exclusion Logic.

Covers: AST negation, SQL compilation, validation, explanation, NULL behavior,
parameter numbering, backward compatibility, and security.
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


# ── AST Negation Tests ─────────────────────────────────────────────────


class TestASTNegation:
    def test_field_rule_default_negated_false(self):
        from segments.models import FieldRule

        r = FieldRule(field="message_count", operator=">=", value=10)
        assert r.negated is False

    def test_field_rule_explicit_negated_true(self):
        from segments.models import FieldRule

        r = FieldRule(field="has_purchased", operator="=", value=True, negated=True)
        assert r.negated is True

    def test_rule_group_default_negated_false(self):
        from segments.models import FieldRule, RuleGroup

        g = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])
        assert g.negated is False

    def test_rule_group_explicit_negated_true(self):
        from segments.models import FieldRule, RuleGroup

        g = RuleGroup(operator="AND", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
            FieldRule(field="total_spend_minor", operator=">=", value=10000),
        ])
        assert g.negated is True

    def test_nested_negated_groups(self):
        from segments.models import FieldRule, RuleGroup

        g = RuleGroup(operator="OR", negated=True, children=[
            RuleGroup(operator="AND", negated=True, children=[
                FieldRule(field="has_purchased", operator="=", value=True),
            ]),
            FieldRule(field="message_count", operator=">=", value=10),
        ])
        assert g.negated is True
        assert g.children[0].negated is True

    def test_backward_compatible_no_negated_field(self):
        from segments.models import FieldRule, RuleGroup

        # Simulate old data without negated field
        old_data = {
            "type": "group",
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "message_count", "operator": ">=", "value": 10}
            ]
        }
        g = RuleGroup(**old_data)
        assert g.negated is False
        assert g.children[0].negated is False

    def test_flatten_rules_with_negated(self):
        from segments.models import FieldRule, RuleGroup, flatten_rules

        tree = RuleGroup(operator="AND", children=[
            FieldRule(field="a", operator="=", value=1, negated=True),
            RuleGroup(operator="OR", children=[
                FieldRule(field="b", operator="=", value=2),
                FieldRule(field="c", operator="=", value=3, negated=True),
            ]),
        ])
        leaves = list(flatten_rules(tree))
        assert len(leaves) == 3
        assert leaves[0].negated is True
        assert leaves[1].negated is False
        assert leaves[2].negated is True


# ── Validation Tests ───────────────────────────────────────────────────


class TestNegationValidation:
    def test_valid_negated_field_rule(self):
        from segments.evaluator import validate_rules
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="has_purchased", operator="=", value=True, negated=True),
        ])
        ok, err = validate_rules(rule)
        assert ok is True
        assert err is None

    def test_valid_negated_group(self):
        from segments.evaluator import validate_rules
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
        ])
        ok, err = validate_rules(rule)
        assert ok is True
        assert err is None

    def test_invalid_negated_type_string(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({
            "type": "rule",
            "field": "has_purchased",
            "operator": "=",
            "value": True,
            "negated": "yes",
        })
        assert ok is False
        assert "negated" in err

    def test_invalid_negated_type_int(self):
        from segments.fields import validate_rule

        ok, err = validate_rule({
            "type": "rule",
            "field": "has_purchased",
            "operator": "=",
            "value": True,
            "negated": 1,
        })
        assert ok is False
        assert "negated" in err

    def test_nested_negated_rules_valid(self):
        from segments.evaluator import validate_rules
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
            RuleGroup(operator="OR", children=[
                FieldRule(field="message_count", operator=">=", value=10, negated=True),
                FieldRule(field="is_blocked", operator="=", value=False),
            ]),
        ])
        ok, err = validate_rules(rule)
        assert ok is True


# ── SQL Compilation Tests ──────────────────────────────────────────────


class TestNegationSQLCompilation:
    def test_not_field_rule(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="has_purchased", operator="=", value=True, negated=True),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        assert params[0] == 1

    def test_not_group(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
            FieldRule(field="total_spend_minor", operator=">=", value=10000),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        assert "AND" in sql
        assert params[0] == 1
        assert True in params
        assert 10000 in params

    def test_not_and_group(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
            FieldRule(field="message_count", operator=">=", value=5),
        ])
        sql, params = compile_rule(1, rule)
        assert sql.startswith("NOT (")
        assert "AND" in sql

    def test_not_or_group(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="OR", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
            FieldRule(field="message_count", operator=">=", value=5),
        ])
        sql, params = compile_rule(1, rule)
        assert sql.startswith("NOT (")
        assert "OR" in sql

    def test_nested_not(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            RuleGroup(operator="OR", negated=True, children=[
                FieldRule(field="has_purchased", operator="=", value=True),
                FieldRule(field="message_count", operator=">=", value=10),
            ]),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql

    def test_mixed_negated_non_negated(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="has_purchased", operator="=", value=True, negated=True),
            FieldRule(field="message_count", operator=">=", value=10),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        assert "u.message_count >= $3" in sql

    def test_sequential_parameter_numbering_with_not(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup
        import re

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="has_purchased", operator="=", value=True, negated=True),
            FieldRule(field="message_count", operator=">=", value=10),
            FieldRule(field="has_tag", operator="=", value="vip"),
        ])
        sql, params = compile_rule(1, rule)
        # has_purchased uses > 0 without params, so params are [1, 10, "vip"]
        assert params[0] == 1
        assert 10 in params
        assert "vip" in params
        nums = [int(m) for m in re.findall(r'\$(\d+)', sql)]
        assert nums == sorted(nums)

    def test_creator_id_preserved_with_not(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="total_spend_minor", operator=">=", value=100, negated=True),
        ])
        sql, params = compile_rule(42, rule)
        assert params[0] == 42

    def test_complex_nested_negation(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="OR", children=[
            RuleGroup(operator="AND", negated=True, children=[
                FieldRule(field="has_purchased", operator="=", value=True),
                FieldRule(field="total_spend_minor", operator=">=", value=10000),
            ]),
            FieldRule(field="message_count", operator=">=", value=10, negated=True),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        assert "OR" in sql


# ── NULL Behavior Tests ────────────────────────────────────────────────


class TestNegationNULLBehavior:
    def test_not_with_is_null(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="assigned_operator_id", operator="is_null", value=None, negated=True),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        assert "IS NULL" in sql

    def test_not_with_inequality(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="funnel_stage", operator="!=", value="vip", negated=True),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        assert "!=" in sql

    def test_not_with_null_value_comparison(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="funnel_stage", operator="=", value="vip", negated=True),
        ])
        sql, params = compile_rule(1, rule)
        assert "NOT (" in sql
        # NULL semantics preserved - NOT doesn't change NULL behavior
        assert params[0] == 1
        assert "vip" in params


# ── Explanation Engine Tests ───────────────────────────────────────────


class TestNegationExplanation:
    @pytest.mark.asyncio
    async def test_explain_negated_field(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        # User has purchased, but rule is negated, so satisfied should be False
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="has_purchased", operator="=", value=True, negated=True),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            result = await explain_rule(1, rule, 42)

        # Outer group is not negated, only the inner FieldRule is
        assert result.negated is False
        assert result.satisfied is False  # True inverted by negation
        assert result.children[0].negated is True
        assert result.children[0].satisfied is False

    @pytest.mark.asyncio
    async def test_explain_negated_group(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        # Both conditions satisfied, but group is negated
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", negated=True, children=[
            FieldRule(field="has_purchased", operator="=", value=True),
            FieldRule(field="message_count", operator=">=", value=10),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            result = await explain_rule(1, rule, 42)

        assert result.negated is True
        assert result.satisfied is False  # all(True, True) = True, then negated = False

    @pytest.mark.asyncio
    async def test_explain_nested_negation(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            RuleGroup(operator="OR", negated=True, children=[
                FieldRule(field="has_purchased", operator="=", value=True),
                FieldRule(field="message_count", operator=">=", value=10),
            ]),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            result = await explain_rule(1, rule, 42)

        # Inner group: any(True, True) = True, then negated = False
        # Outer AND: [False] => False
        assert result.children[0].negated is True
        assert result.children[0].satisfied is False
        assert result.satisfied is False

    @pytest.mark.asyncio
    async def test_explain_negated_flag_in_response(self):
        from segments.evaluator import explain_rule
        from segments.models import FieldRule, RuleGroup

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=False)
        pool = _make_pool(mock_conn)

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="has_purchased", operator="=", value=True, negated=True),
        ])

        with patch("segments.evaluator.get_pool", return_value=pool):
            result = await explain_rule(1, rule, 42)

        # User hasn't purchased, so base result is False, negated = True
        assert result.children[0].negated is True
        assert result.children[0].satisfied is True  # False inverted by negation
        assert result.satisfied is True


# ── Backward Compatibility Tests ───────────────────────────────────────


class TestBackwardCompatibility:
    def test_old_rules_without_negated_still_work(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        # Simulate old data format
        old_rule = RuleGroup(operator="AND", children=[
            FieldRule(field="message_count", operator=">=", value=10),
        ])
        sql, params = compile_rule(1, old_rule)
        assert "u.message_count >= $2" in sql
        assert params == [1, 10]

    def test_old_nested_rules_still_work(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        old_rule = RuleGroup(operator="OR", children=[
            RuleGroup(operator="AND", children=[
                FieldRule(field="message_count", operator=">=", value=10),
                FieldRule(field="has_tag", operator="=", value="vip"),
            ]),
            FieldRule(field="is_blocked", operator="=", value=False),
        ])
        sql, params = compile_rule(1, old_rule)
        assert "AND" in sql
        assert "OR" in sql

    def test_existing_segment_api_compatible(self):
        from segments.models import SegmentCreate, SegmentRule

        # Old format without negated
        old_rules = {
            "type": "group",
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "message_count", "operator": ">=", "value": 10}
            ]
        }
        seg = SegmentCreate(name="Test", rules=SegmentRule(**old_rules))
        assert seg.rules.negated is False
        assert seg.rules.children[0].negated is False

    def test_new_negated_format_works(self):
        from segments.models import SegmentCreate, SegmentRule

        new_rules = {
            "type": "group",
            "operator": "AND",
            "children": [
                {"type": "rule", "field": "has_purchased", "operator": "=", "value": True, "negated": True}
            ]
        }
        seg = SegmentCreate(name="Test", rules=SegmentRule(**new_rules))
        assert seg.rules.children[0].negated is True


# ── Security Tests ─────────────────────────────────────────────────────


class TestNegationSecurity:
    def test_not_does_not_allow_sql_injection(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="funnel_stage", operator="=", value="'; DROP TABLE users; --", negated=True),
        ])
        sql, params = compile_rule(1, rule)
        # Value is parameterized, not interpolated
        assert "'; DROP TABLE users; --" in params
        assert "DROP" not in sql

    def test_not_does_not_allow_arbitrary_sql(self):
        from segments.evaluator import CompilationError, SQLCompiler
        from segments.models import FieldRule, RuleGroup

        compiler = SQLCompiler(1)
        with pytest.raises(CompilationError):
            compiler.compile(RuleGroup(operator="AND", children=[
                FieldRule(field="1=1; DROP TABLE users; --", operator=">=", value=10, negated=True),
            ]))

    def test_not_preserves_parameterization(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="funnel_stage", operator="=", value="vip", negated=True),
        ])
        sql, params = compile_rule(1, rule)
        # All user values are in params, not in SQL
        assert "vip" in params
        assert "'vip'" not in sql

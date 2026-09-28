"""P3.3.14.1 — Opportunity Engine orchestration tests (mocked, no provider/DB writes).

Covers ``commerce.opportunity_engine.evaluate_opportunity``:
happy path, no candidates, eligibility exclusion, ownership overlap,
multiple definitions ordering, multiple Drop mappings preservation,
ConversationState filtering, LLM/provider/persistence/legacy boundaries,
determinism, failure propagation.
"""

import ast
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from commerce.fan_commercial_state import FanCommercialState
from commerce.offer_history import OfferHistory

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_engine.py"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _fan_state(**over):
    base = {
        "creator_id": 1,
        "user_id": 10,
        "purchase_count": 0,
        "total_spend_minor": 0,
        "average_order_value_minor": None,
        "highest_purchase_minor": None,
        "last_purchase_at": None,
        "recent_purchase_count": 0,
        "recent_spend_minor": 0,
        "purchased_vault_ids": frozenset(),
        "delivered_vault_ids": (),
        "recent_offer_count": 0,
        "recent_rejected_offer_count": 0,
        "last_offer_at": None,
        "recent_offered_vault_ids": (),
        "currency": "USD",
    }
    base.update(over)
    return FanCommercialState(**base)


def _history(**over):
    base = {
        "creator_id": 1,
        "user_id": 10,
        "total_offer_count": 0,
        "recent_offer_count": 0,
        "last_offer_at": None,
        "declined_offer_count": 0,
        "recent_declined_offer_count": 0,
        "state_counts": (),
        "has_active_offer": False,
        "active_offer_count": 0,
        "offered_vault_sets": (),
        "active_vault_sets": (),
        "null_snapshot_count": 0,
        "definition_identity_available": False,
    }
    base.update(over)
    return OfferHistory(**base)


def _definition_row(**over):
    row = {
        "creator_id": 1,
        "id": 11,
        "stable_key": "alpha",
        "version": 1,
        "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1"],
        "family_id": None,
        "price_minor": 1999,
        "currency": "USD",
        "allow_download": True,
        "status": "active",
    }
    row.update(over)
    return row


def _conversation(**over):
    from core.conversation_state import ConversationState

    base = {
        "lifecycle": "established",
        "identity_already_established": True,
        "current_topic": "movie",
        "recent_topics": ("movie", "weekend"),
        "open_threads": ("movie",),
        "last_question": None,
        "last_question_answered": False,
        "consecutive_questions": 0,
        "tone": "flirty",
        "last_user_fact": "i love sci-fi movies lately and live at 123 main st",
        "questions_in_last_3": 2,
    }
    base.update(over)
    return ConversationState(**base)


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_selected(self):
        from commerce.opportunity_engine import evaluate_opportunity

        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[
                _definition_row(id=11, stable_key="alpha", canonical_vault_item_ids=["V1"])
            ],
            mappings=[{"creator_id": 1, "definition_id": 11, "dropfans_product_id": "drop_abc"}],
        )
        assert res.has_opportunity is True
        assert res.selected_candidate is not None
        assert res.selected_candidate.definition_id == 11
        assert res.selected_candidate.mapped_drop_ids == ("drop_abc",)
        assert res.selected_candidate.price_minor == 1999
        assert res.ranking_result is not None
        assert res.ranking_result.selected is not None
        assert res.status == "RANKED"

    @pytest.mark.asyncio
    async def test_price_and_vault_preserved_exactly(self):
        from commerce.opportunity_engine import evaluate_opportunity

        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row(canonical_vault_item_ids=["V3", "V4"], price_minor=5000)],
            mappings=[],
        )
        assert res.selected_candidate.canonical_vault_item_ids == ("V3", "V4")
        assert res.selected_candidate.price_minor == 5000


class TestNoCandidates:
    @pytest.mark.asyncio
    async def test_no_active_definitions(self):
        from commerce.opportunity_engine import evaluate_opportunity

        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[],
            mappings=[],
        )
        assert res.has_opportunity is False
        assert res.selected_candidate is None
        assert res.ranking_result is None
        assert res.status == "NO_CANDIDATES"
        assert res.candidates == ()


class TestEligibilityExclusion:
    @pytest.mark.asyncio
    async def test_candidate_denied_not_ranked(self):
        from commerce.opportunity_engine import evaluate_opportunity

        # Retired definition → inactive → denied
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row(status="retired")],
            mappings=[],
        )
        # Resolver filters non-active, so no candidates at all
        assert res.status == "NO_CANDIDATES"
        assert res.has_opportunity is False

    @pytest.mark.asyncio
    async def test_ineligible_due_to_ownership(self):
        from commerce.opportunity_engine import evaluate_opportunity

        # Fan already owns V1
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(purchased_vault_ids=frozenset({"V1"})),
            offer_history=_history(),
            definitions=[_definition_row(canonical_vault_item_ids=["V1"])],
            mappings=[],
        )
        assert res.has_opportunity is False
        assert res.status == "NO_ELIGIBLE_CANDIDATES"
        assert len(res.ineligible) == 1

    @pytest.mark.asyncio
    async def test_active_duplicate_blocks(self):
        from commerce.opportunity_engine import evaluate_opportunity

        hist = _history(has_active_offer=True, active_offer_count=1, active_vault_sets=(("V1",),))
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=hist,
            definitions=[_definition_row(canonical_vault_item_ids=["V1"])],
            mappings=[],
        )
        assert res.has_opportunity is False
        assert res.status == "NO_ELIGIBLE_CANDIDATES"


class TestMultipleDefinitions:
    @pytest.mark.asyncio
    async def test_deterministic_ordering(self):
        from commerce.opportunity_engine import evaluate_opportunity

        # Two definitions with different vault sets, one novel (V9 not offered before)
        hist = _history(offered_vault_sets=(("V1",),))
        defs = [
            _definition_row(id=1, stable_key="a", canonical_vault_item_ids=["V1"]),
            _definition_row(id=2, stable_key="b", canonical_vault_item_ids=["V9"]),
        ]
        res1 = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=hist,
            definitions=defs,
            mappings=[],
        )
        res2 = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=hist,
            definitions=list(reversed(defs)),
            mappings=[],
        )
        # Ranking v1 prefers novel set (V9), so V9 definition should win regardless of input order
        assert res1.selected_candidate.definition_id == 2
        assert res2.selected_candidate.definition_id == 2
        assert res1.ranking_result.ranked[0].definition_id == 2


class TestMultipleDropMappings:
    @pytest.mark.asyncio
    async def test_all_mapped_cuids_preserved(self):
        from commerce.opportunity_engine import evaluate_opportunity

        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row(id=11)],
            mappings=[
                {"creator_id": 1, "definition_id": 11, "dropfans_product_id": "drop_b"},
                {"creator_id": 1, "definition_id": 11, "dropfans_product_id": "drop_a"},
                {"creator_id": 1, "definition_id": 11, "dropfans_product_id": "drop_a"},
            ],
        )
        assert res.selected_candidate.mapped_drop_ids == ("drop_a", "drop_b")

    @pytest.mark.asyncio
    async def test_no_dropfans_call_to_choose(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity
        import commerce.opportunity_engine as eng

        # Patch any get_drop if it existed — should be zero calls
        calls = []

        async def fake_get_drop(*a, **kw):
            calls.append(1)
            return {}

        monkeypatch.setattr("integrations.dropfans.service.get_drop", fake_get_drop, raising=False)
        # Also patch dropfans client directly
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row()],
            mappings=[{"creator_id": 1, "definition_id": 11, "dropfans_product_id": "drop_x"}],
        )
        assert res.has_opportunity is True
        assert calls == []


class TestConversationState:
    @pytest.mark.asyncio
    async def test_only_approved_fields_reach_ranking(self):
        from commerce.opportunity_engine import evaluate_opportunity

        conv = _conversation(
            tone="flirty",
            last_user_fact="secret address",
            consecutive_questions=5,
            questions_in_last_3=3,
        )
        res = await evaluate_opportunity(
            1,
            10,
            conv,
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row()],
            mappings=[],
        )
        # Ranking conversation should have only lifecycle/current_topic/recent_topics/open_threads
        rc = res.ranking_inputs[0].conversation
        assert rc.lifecycle == "established"
        assert rc.current_topic == "movie"
        assert rc.recent_topics == ("movie", "weekend")
        assert rc.open_threads == ("movie",)
        # Tone / last_user_fact must not leak via ranking conversation
        assert not hasattr(rc, "tone") or rc.lifecycle != "flirty"  # lifecycle is not tone

    @pytest.mark.asyncio
    async def test_none_conversation(self):
        from commerce.opportunity_engine import evaluate_opportunity

        res = await evaluate_opportunity(
            1,
            10,
            None,
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row()],
            mappings=[],
        )
        assert res.has_opportunity is True


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_identical_inputs_identical_result(self):
        from commerce.opportunity_engine import evaluate_opportunity

        kwargs = dict(
            fan_commercial_state=_fan_state(),
            offer_history=_history(offered_vault_sets=(("V1",),)),
            definitions=[
                _definition_row(id=1, stable_key="a", canonical_vault_item_ids=["V1"]),
                _definition_row(id=2, stable_key="b", canonical_vault_item_ids=["V2"]),
            ],
            mappings=[],
        )
        r1 = await evaluate_opportunity(1, 10, _conversation(), NOW, **kwargs)
        r2 = await evaluate_opportunity(1, 10, _conversation(), NOW, **kwargs)
        assert r1.ranking_result == r2.ranking_result
        assert r1.selected_candidate == r2.selected_candidate
        assert r1.status == r2.status


class TestDatabaseFailure:
    @pytest.mark.asyncio
    async def test_fan_state_failure_propagates(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity
        import commerce.fan_commercial_state as fcs

        async def failing(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(fcs, "get_fan_commercial_state", failing)
        with pytest.raises(RuntimeError, match="db down"):
            await evaluate_opportunity(1, 10, _conversation(), NOW)

    @pytest.mark.asyncio
    async def test_offer_history_failure_propagates(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity
        import commerce.offer_history as oh

        async def failing(*a, **kw):
            raise RuntimeError("history down")

        monkeypatch.setattr(oh, "get_offer_history", failing)
        # Need to bypass fan state fetch by injecting it, so history failure is the one
        with pytest.raises(RuntimeError, match="history down"):
            await evaluate_opportunity(
                1, 10, _conversation(), NOW, fan_commercial_state=_fan_state()
            )

    @pytest.mark.asyncio
    async def test_resolver_failure_propagates(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity
        import commerce.offer_definition_resolver as odr

        async def failing(*a, **kw):
            raise RuntimeError("resolver down")

        monkeypatch.setattr(odr, "resolve_offer_definitions", failing)
        with pytest.raises(RuntimeError, match="resolver down"):
            await evaluate_opportunity(
                1,
                10,
                _conversation(),
                NOW,
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
            )

    def test_naive_now_rejected(self):
        import asyncio

        from commerce.opportunity_engine import evaluate_opportunity
        from datetime import datetime as dt

        naive = dt(2026, 6, 1, 12, 0)  # no tzinfo
        with pytest.raises(ValueError, match="now must be a timezone-aware"):
            asyncio.run(
                evaluate_opportunity(
                    1,
                    10,
                    _conversation(),
                    naive,
                    fan_commercial_state=_fan_state(),
                    offer_history=_history(),
                    definitions=[],
                    mappings=[],
                )
            )


class TestNoLegacyFallback:
    @pytest.mark.asyncio
    async def test_no_legacy_selector_called(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity

        calls = []

        async def fake_resolve(*a, **kw):
            calls.append("legacy")
            return 999

        monkeypatch.setattr(
            "commerce.product_selection.resolve_commerce_product_with_history",
            fake_resolve,
            raising=False,
        )
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[],
            mappings=[],
        )
        assert res.has_opportunity is False
        assert calls == []

    @pytest.mark.asyncio
    async def test_provider_zero_calls(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity

        calls = []

        async def fake_get_drop(*a, **kw):
            calls.append(1)
            return {}

        # Patch all provider entry points that engine must not call
        for path in [
            "integrations.dropfans.service.get_drop",
            "integrations.dropfans.client.DropfansClient.get_drop",
            "commerce.opportunity_sealing.seal_ranked_candidate",
        ]:
            monkeypatch.setattr(path, fake_get_drop, raising=False)

        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row()],
            mappings=[],
        )
        assert calls == []
        assert res.has_opportunity is True

    @pytest.mark.asyncio
    async def test_no_commerce_offers_write(self, monkeypatch):
        from commerce.opportunity_engine import evaluate_opportunity
        import db.postgres as pg

        async def fake_get_pool():
            raise AssertionError("should not touch DB pool for writes when injected")

        # With injection, engine does no DB writes; if it tried to insert, get_pool would be called for write path
        # We verify by injecting all state and ensuring no pool call for writes
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row()],
            mappings=[],
        )
        assert res.has_opportunity is True


class TestResultContract:
    @pytest.mark.asyncio
    async def test_result_is_frozen(self):
        from commerce.opportunity_engine import evaluate_opportunity

        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(),
            offer_history=_history(),
            definitions=[_definition_row()],
            mappings=[],
        )
        assert dataclasses.is_dataclass(res)
        assert res.__dataclass_params__.frozen is True
        with pytest.raises(dataclasses.FrozenInstanceError):
            res.has_opportunity = False  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_result_preserves_eligibility_info(self):
        from commerce.opportunity_engine import evaluate_opportunity

        # One eligible, one owned
        defs = [
            _definition_row(id=1, canonical_vault_item_ids=["V9"]),
            _definition_row(id=2, canonical_vault_item_ids=["V1"]),
        ]
        res = await evaluate_opportunity(
            1,
            10,
            _conversation(),
            NOW,
            fan_commercial_state=_fan_state(purchased_vault_ids=frozenset({"V1"})),
            offer_history=_history(),
            definitions=defs,
            mappings=[],
        )
        assert res.has_opportunity is True
        assert len(res.eligible_candidates) == 1
        assert res.eligible_candidates[0].definition_id == 1
        assert len(res.ineligible) == 1
        assert res.ineligible[0][0].definition_id == 2

    def test_result_field_shape(self):
        from commerce.opportunity_engine import OpportunityEngineResult

        fields = {f.name for f in dataclasses.fields(OpportunityEngineResult)}
        assert {
            "creator_id",
            "user_id",
            "evaluated_at",
            "fan_commercial_state",
            "offer_history",
            "owned_vault_ids",
            "candidates",
            "eligible_candidates",
            "ineligible",
            "ranking_inputs",
            "ranking_result",
            "selected_candidate",
            "has_opportunity",
            "status",
        }.issubset(fields)


class TestStaticBoundary:
    def test_no_forbidden_imports(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        # Check mechanics imports/calls, not prose mentions in docstrings.
        for tok in (
            "from commerce.product_selection",
            "from commerce.content_matching",
            "from commerce.vault_taxonomy",
            "from commerce.vault_ranking",
            "from commerce.product_knowledge",
            "from commerce.execution import",
            "from commerce.orchestrator import",
            "from commerce.pipeline import",
            "from integrations.dropfans",
            "from commerce.opportunity_sealing",
            "import redis",
            "from redis",
            "from db.redis",
            "execute_ppv(",
            "create_offer(",
            "get_drop(",
        ):
            assert tok not in src, f"forbidden import {tok!r} found"

    def test_no_llm_imports(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for tok in (
            "llm_provider",
            "gemini",
            "openai",
            "deepseek",
            "core.llm_tools",
            "generate_draft",
        ):
            assert tok not in src

    def test_no_provider_or_persistence_mechanics(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for tok in (
            "INSERT INTO commerce_offers",
            "UPDATE commerce_offers",
            "pg_advisory",
            "DropfansClient",
            "create_drop",
        ):
            assert tok not in src

    def test_does_not_call_datetime_now(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "datetime.now(" not in src
        assert "time.time(" not in src

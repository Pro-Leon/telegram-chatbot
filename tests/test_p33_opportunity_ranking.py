"""P3.3.12 — deterministic ranking tests (pure, no I/O).

Covers ``commerce.opportunity_ranking``: assembler scope/eligibility/
preservation semantics, lexicographic policy determinism (novelty,
recent-item avoidance, offer-type order, stable identity tie-break),
result shape/explainability, and the no-legacy/no-LLM/no-provider/
no-Redis production boundary. All facts are constructed directly; no
database, provider, or clock is touched.
"""

import ast
import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from commerce.fan_commercial_state import FanCommercialState
from commerce.offer_history import OfferHistory
from commerce.opportunity import candidate_from_definition
from commerce.opportunity_eligibility import evaluate_opportunity_eligibility
from commerce.opportunity_ranking import (
    NOT_RECENTLY_OFFERED,
    NOVEL_CANONICAL_SET,
    OFFER_TYPE_DIVERSITY,
    PREVIOUSLY_OFFERED_SET,
    RANKING_FACTORS,
    RANKING_POLICY_VERSION,
    RECENTLY_OFFERED_ITEMS,
    STABLE_ID_TIEBREAK,
    OpportunityRankingInput,
    OpportunityRankingResult,
    RankedCandidate,
    RankingConversationContext,
    RankingKeys,
    assemble_ranking_input,
    rank_candidates,
)
from core.conversation_state import ConversationState

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_ranking.py"

STAMP = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
STAMP2 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def _definition(**over):
    row = {
        "id": 11,
        "creator_id": 1,
        "stable_key": "black-lingerie",
        "version": 1,
        "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1", "V2"],
        "family_id": None,
        "price_minor": 1999,
        "currency": "USD",
        "allow_download": True,
        "status": "active",
    }
    row.update(over)
    return row


def _candidate(**over):
    return candidate_from_definition(1, 10, _definition(**over))


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


def _verdict(candidate, history=None):
    return evaluate_opportunity_eligibility(candidate, frozenset(), history or _history())


def _entry(candidate=None, **over):
    cand = candidate or _candidate()
    hist = over.pop("history", _history())
    fan = over.pop("fan_state", _fan_state())
    conv = over.pop("conversation", None)
    stamp = over.pop("evaluated_at", STAMP)
    assert not over, f"unexpected overrides: {over}"
    return assemble_ranking_input(
        cand,
        eligibility_verdict=_verdict(cand, hist),
        fan_commercial_state=fan,
        offer_history=hist,
        conversation=conv,
        evaluated_at=stamp,
    )


def _conversation(**over):
    base = {
        "lifecycle": "established",
        "identity_already_established": True,
        "current_topic": "movie",
        "recent_topics": ("movie", "weekend"),
        "open_threads": ("movie",),
        "last_question": None,
        "last_question_answered": False,
        "consecutive_questions": 0,
        "tone": "warm",
        "last_user_fact": None,
        "questions_in_last_3": 0,
    }
    base.update(over)
    return ConversationState(**base)


class TestAssemblerScope:
    def test_fan_state_creator_mismatch_raises(self):
        cand = _candidate()
        with pytest.raises(ValueError, match="creator scope"):
            assemble_ranking_input(
                cand,
                eligibility_verdict=_verdict(cand),
                fan_commercial_state=_fan_state(creator_id=2),
                offer_history=_history(),
                evaluated_at=STAMP,
            )

    def test_history_user_mismatch_raises(self):
        cand = _candidate()
        with pytest.raises(ValueError, match="user scope"):
            assemble_ranking_input(
                cand,
                eligibility_verdict=_verdict(cand),
                fan_commercial_state=_fan_state(),
                offer_history=_history(user_id=99),
                evaluated_at=STAMP,
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"creator_id": 0},
            {"creator_id": -1},
            {"user_id": True},
        ],
    )
    def test_invalid_candidate_scope_raises(self, kwargs):
        cand = _candidate()
        broken = dataclasses.replace(cand, **kwargs)
        with pytest.raises(ValueError):
            assemble_ranking_input(
                broken,
                eligibility_verdict=_verdict(cand),
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                evaluated_at=STAMP,
            )


class TestAssemblerEligibility:
    def test_ineligible_verdict_raises(self):
        cand = _candidate()
        bad = evaluate_opportunity_eligibility(cand, frozenset({"V1", "V2"}), _history())
        assert not bad.eligible
        with pytest.raises(ValueError, match="not eligible"):
            assemble_ranking_input(
                cand,
                eligibility_verdict=bad,
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                evaluated_at=STAMP,
            )

    def test_missing_inputs_raise_type_error(self):
        cand = _candidate()
        good = _verdict(cand)
        with pytest.raises(TypeError):
            assemble_ranking_input(
                None,
                eligibility_verdict=good,
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                evaluated_at=STAMP,
            )
        with pytest.raises(TypeError):
            assemble_ranking_input(
                cand,
                eligibility_verdict=None,
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                evaluated_at=STAMP,
            )
        with pytest.raises(TypeError):
            assemble_ranking_input(
                cand,
                eligibility_verdict=good,
                fan_commercial_state=None,
                offer_history=_history(),
                evaluated_at=STAMP,
            )
        with pytest.raises(TypeError):
            assemble_ranking_input(
                cand,
                eligibility_verdict=good,
                fan_commercial_state=_fan_state(),
                offer_history=None,
                evaluated_at=STAMP,
            )

    def test_non_unverified_provider_claim_raises(self):
        cand = _candidate()
        claimed = dataclasses.replace(cand, provider_verification="verified")
        with pytest.raises(ValueError, match="unverified"):
            assemble_ranking_input(
                claimed,
                eligibility_verdict=_verdict(cand),
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                evaluated_at=STAMP,
            )

    @pytest.mark.parametrize(
        "stamp", [None, "2026-06-01", datetime(2026, 6, 1, 12, 0)]  # noqa: DTZ001
    )  # naive datetime is a deliberate invalid-input case
    def test_timestamp_required_and_aware(self, stamp):
        cand = _candidate()
        with pytest.raises(ValueError, match="evaluated_at"):
            assemble_ranking_input(
                cand,
                eligibility_verdict=_verdict(cand),
                fan_commercial_state=_fan_state(),
                offer_history=_history(),
                evaluated_at=stamp,
            )


class TestAssemblerPreservation:
    def test_price_and_vault_ids_preserved_exactly(self):
        cand = _candidate(price_minor=4999, canonical_vault_item_ids=["V3", "V4"])
        entry = _entry(cand)
        assert entry.candidate is cand
        assert entry.candidate.price_minor == 4999
        assert entry.candidate.canonical_vault_item_ids == ("V3", "V4")
        assert entry.candidate.currency == "USD"

    def test_facts_held_by_reference_not_duplicated(self):
        cand = _candidate()
        fan, hist = _fan_state(), _history()
        verdict = _verdict(cand)
        entry = assemble_ranking_input(
            cand,
            eligibility_verdict=verdict,
            fan_commercial_state=fan,
            offer_history=hist,
            evaluated_at=STAMP,
        )
        assert entry.eligibility_verdict is verdict
        assert entry.fan_commercial_state is fan
        assert entry.offer_history is hist

    def test_inputs_not_mutated(self):
        cand = _candidate()
        fan, hist = _fan_state(), _history()
        verdict = _verdict(cand)
        before = (cand, fan, hist, verdict)
        assemble_ranking_input(
            cand,
            eligibility_verdict=verdict,
            fan_commercial_state=fan,
            offer_history=hist,
            conversation=_conversation(),
            evaluated_at=STAMP,
        )
        assert (cand, fan, hist, verdict) == before

    def test_input_is_frozen(self):
        assert OpportunityRankingInput.__dataclass_params__.frozen is True
        entry = _entry()
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.creator_id = 2  # type: ignore[misc]


class TestAssemblerConversation:
    def test_only_admissible_fields_extracted(self):
        conv = _conversation(
            tone="flirty",
            last_user_fact="i love sci-fi movies lately",
            consecutive_questions=4,
            questions_in_last_3=3,
        )
        entry = _entry(conversation=conv)
        assert entry.conversation == RankingConversationContext(
            lifecycle="established",
            current_topic="movie",
            recent_topics=("movie", "weekend"),
            open_threads=("movie",),
        )
        assert RankingConversationContext.__dataclass_params__.frozen is True

    def test_llm_floats_and_scores_ignored(self):
        noisy = {
            "lifecycle": "established",
            "current_topic": "movie",
            "recent_topics": ["movie"],
            "open_threads": ["movie"],
            "purchase_intent": 0.9,
            "price_interest": 0.8,
            "content_interest": 0.7,
            "relationship_score": 0.75,
            "temperature": "hot",
            "tone": "flirty",
        }
        plain = {
            k: noisy[k] for k in ("lifecycle", "current_topic", "recent_topics", "open_threads")
        }
        assert _entry(conversation=noisy) == _entry(conversation=plain)

    def test_none_conversation_yields_empty_context(self):
        entry = _entry(conversation=None)
        assert entry.conversation == RankingConversationContext()

    def test_conversation_weightless_in_v1(self):
        batch_a = [
            _entry(conversation=_conversation(current_topic="movie")),
            _entry(
                _candidate(id=2, stable_key="bbb", canonical_vault_item_ids=["V3"]),
                conversation=_conversation(current_topic="movie"),
            ),
        ]
        batch_b = [
            _entry(conversation=_conversation(current_topic="weather")),
            _entry(
                _candidate(id=2, stable_key="bbb", canonical_vault_item_ids=["V3"]),
                conversation=_conversation(current_topic="weather"),
            ),
        ]
        assert rank_candidates(batch_a, evaluated_at=STAMP) == rank_candidates(
            batch_b, evaluated_at=STAMP
        )


class TestRankingKeys:
    def test_novelty_and_overlap_derivation(self):
        cand = _candidate(canonical_vault_item_ids=["V1", "V2"])
        hist = _history(offered_vault_sets=(("V1", "V2"),))
        fan = _fan_state(recent_offered_vault_ids=("V2", "V9"))
        entry = _entry(cand, history=hist, fan_state=fan)
        assert entry.keys == RankingKeys(novel_set=False, recent_item_overlap=1, type_order=2)

    def test_single_type_order_value(self):
        entry = _entry()
        assert entry.keys.type_order == 2  # SINGLE
        assert RankingKeys.__dataclass_params__.frozen is True


class TestPolicyOrdering:
    def _batch(self):
        novel = _candidate(id=1, stable_key="aaa", canonical_vault_item_ids=["V7", "V8"])
        stale = _candidate(id=2, stable_key="bbb", canonical_vault_item_ids=["V1", "V2"])
        hist = _history(offered_vault_sets=(("V1", "V2"),))
        return [
            _entry(stale, history=hist),
            _entry(novel, history=hist),
        ]

    def test_deterministic_repeated_execution(self):
        batch = self._batch()
        first = rank_candidates(batch, evaluated_at=STAMP)
        second = rank_candidates(batch, evaluated_at=STAMP)
        assert first == second
        assert [r.definition_id for r in first.ranked] == [1, 2]
        assert first.selected is not None and first.selected.definition_id == 1

    def test_novel_candidate_preferred_with_exact_factors(self):
        result = rank_candidates(self._batch(), evaluated_at=STAMP)
        winner, loser = result.ranked
        assert winner.factors == (NOVEL_CANONICAL_SET, NOT_RECENTLY_OFFERED, STABLE_ID_TIEBREAK)
        assert loser.factors == (PREVIOUSLY_OFFERED_SET, NOT_RECENTLY_OFFERED, STABLE_ID_TIEBREAK)

    def test_recent_overlap_avoidance(self):
        old = _candidate(id=1, stable_key="aaa", canonical_vault_item_ids=["V5"])
        fresh = _candidate(id=2, stable_key="bbb", canonical_vault_ids=["V6"])
        fan = _fan_state(recent_offered_vault_ids=("V5", "V9"))
        result = rank_candidates(
            [_entry(old, fan_state=fan), _entry(fresh, fan_state=fan)],
            evaluated_at=STAMP,
        )
        assert [r.definition_id for r in result.ranked] == [2, 1]
        assert result.ranked[0].factors == (
            NOVEL_CANONICAL_SET,
            NOT_RECENTLY_OFFERED,
            STABLE_ID_TIEBREAK,
        )
        assert result.ranked[1].factors == (
            NOVEL_CANONICAL_SET,
            RECENTLY_OFFERED_ITEMS,
            STABLE_ID_TIEBREAK,
        )
        assert result.ranked[1].recent_item_overlap == 1

    def test_type_order_breaks_ties_and_emits_diversity(self):
        single = _candidate(id=1, stable_key="mmm", offer_type="SINGLE")
        core = _candidate(
            id=2, stable_key="mmm", offer_type="CORE_BUNDLE", canonical_vault_item_ids=["V3"]
        )
        hist = _history()
        result = rank_candidates(
            [_entry(single, history=hist), _entry(core, history=hist)],
            evaluated_at=STAMP,
        )
        assert [r.definition_id for r in result.ranked] == [2, 1]
        assert OFFER_TYPE_DIVERSITY in result.ranked[0].factors

    def test_single_type_batch_omits_diversity_factor(self):
        a = _candidate(id=1, stable_key="aaa")
        b = _candidate(id=2, stable_key="bbb", canonical_vault_item_ids=["V3"])
        result = rank_candidates([_entry(a), _entry(b)], evaluated_at=STAMP)
        assert [r.definition_id for r in result.ranked] == [1, 2]
        for ranked in result.ranked:
            assert OFFER_TYPE_DIVERSITY not in ranked.factors
            assert STABLE_ID_TIEBREAK in ranked.factors

    def test_stable_identity_tiebreak(self):
        a = _candidate(id=9, stable_key="zzz", version=1)
        b = _candidate(id=3, stable_key="zzz", version=2, canonical_vault_item_ids=["V4"])
        c = _candidate(id=5, stable_key="aaa", version=1, canonical_vault_item_ids=["V5"])
        result = rank_candidates([_entry(a), _entry(b), _entry(c)], evaluated_at=STAMP)
        assert [(r.stable_key, r.version, r.definition_id) for r in result.ranked] == [
            ("aaa", 1, 5),
            ("zzz", 1, 9),
            ("zzz", 2, 3),
        ]

    def test_timestamp_only_stamps_never_scores(self):
        early_batch = self._batch()
        late_batch = [
            assemble_ranking_input(
                e.candidate,
                eligibility_verdict=e.eligibility_verdict,
                fan_commercial_state=e.fan_commercial_state,
                offer_history=e.offer_history,
                conversation=None,
                evaluated_at=STAMP2,
            )
            for e in early_batch
        ]
        early = rank_candidates(early_batch, evaluated_at=STAMP)
        late = rank_candidates(late_batch, evaluated_at=STAMP2)
        assert [r.definition_id for r in early.ranked] == [r.definition_id for r in late.ranked]
        assert [r.factors for r in early.ranked] == [r.factors for r in late.ranked]
        assert early.evaluated_at == STAMP and late.evaluated_at == STAMP2
        assert early != late

    def test_empty_input_selects_none(self):
        result = rank_candidates([], evaluated_at=STAMP)
        assert result.ranked == () and result.selected is None
        assert result.policy_version == RANKING_POLICY_VERSION

    def test_batch_scope_and_timestamp_guards(self):
        a = _entry()
        other_user = dataclasses.replace(_candidate(), user_id=11)
        other_entry = assemble_ranking_input(
            other_user,
            eligibility_verdict=evaluate_opportunity_eligibility(
                other_user, frozenset(), _history(user_id=11)
            ),
            fan_commercial_state=_fan_state(user_id=11),
            offer_history=_history(user_id=11),
            evaluated_at=STAMP,
        )
        with pytest.raises(ValueError, match="scope"):
            rank_candidates([a, other_entry], evaluated_at=STAMP)
        with pytest.raises(ValueError, match="timestamp"):
            rank_candidates([a], evaluated_at=STAMP2)
        with pytest.raises(TypeError):
            rank_candidates(["not-an-input"], evaluated_at=STAMP)  # type: ignore[list-item]


class TestForbiddenInference:
    def test_family_never_gates_or_reorders(self):
        plain = _candidate(id=1, stable_key="aaa", family_id=None)
        familied = _candidate(id=2, stable_key="bbb", family_id=42, canonical_vault_item_ids=["V3"])
        result = rank_candidates([_entry(plain), _entry(familied)], evaluated_at=STAMP)
        assert [r.definition_id for r in result.ranked] == [1, 2]

    def test_delivery_never_becomes_ownership(self):
        cand = _candidate()
        undelivered = _entry(cand, fan_state=_fan_state(delivered_vault_ids=()))
        delivered = _entry(cand, fan_state=_fan_state(delivered_vault_ids=("V1", "V2")))
        assert rank_candidates([undelivered], evaluated_at=STAMP) == rank_candidates(
            [delivered], evaluated_at=STAMP
        )

    def test_no_affordability_or_price_ordering(self):
        cheap = _candidate(id=1, stable_key="aaa", price_minor=500)
        pricey = _candidate(
            id=2, stable_key="bbb", price_minor=75000, canonical_vault_item_ids=["V3"]
        )
        whale = _fan_state(
            purchase_count=50,
            total_spend_minor=500000,
            average_order_value_minor=10000,
            highest_purchase_minor=75000,
        )
        broke = _fan_state()
        rich_result = rank_candidates(
            [_entry(pricey, fan_state=whale), _entry(cheap, fan_state=whale)], evaluated_at=STAMP
        )
        poor_result = rank_candidates(
            [_entry(pricey, fan_state=broke), _entry(cheap, fan_state=broke)], evaluated_at=STAMP
        )
        assert [r.definition_id for r in rich_result.ranked] == [1, 2]
        assert [r.definition_id for r in poor_result.ranked] == [1, 2]
        # Prices untouched by assembly and ranking.
        assert cheap.price_minor == 500 and pricey.price_minor == 75000


class TestResultContract:
    def test_shapes_and_immutability(self):
        assert {f.name for f in dataclasses.fields(RankedCandidate)} == {
            "definition_id",
            "stable_key",
            "version",
            "factors",
            "novel_set",
            "recent_item_overlap",
        }
        assert OpportunityRankingResult.__dataclass_params__.frozen is True
        assert RankedCandidate.__dataclass_params__.frozen is True
        result = rank_candidates(self._ordered_batch(), evaluated_at=STAMP)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.selected = None  # type: ignore[misc]

    def _ordered_batch(self):
        novel = _candidate(id=1, stable_key="aaa", canonical_vault_item_ids=["V7", "V8"])
        stale = _candidate(id=2, stable_key="bbb", canonical_vault_item_ids=["V1", "V2"])
        hist = _history(offered_vault_sets=(("V1", "V2"),))
        return [_entry(stale, history=hist), _entry(novel, history=hist)]

    def test_selected_is_first_with_scope(self):
        result = rank_candidates(self._ordered_batch(), evaluated_at=STAMP)
        assert result.selected == result.ranked[0]
        assert (result.creator_id, result.user_id) == (1, 10)
        assert result.evaluated_at == STAMP

    def test_factors_closed_vocabulary(self):
        result = rank_candidates(self._ordered_batch(), evaluated_at=STAMP)
        assert RANKING_FACTORS == frozenset(
            {
                NOVEL_CANONICAL_SET,
                PREVIOUSLY_OFFERED_SET,
                NOT_RECENTLY_OFFERED,
                RECENTLY_OFFERED_ITEMS,
                OFFER_TYPE_DIVERSITY,
                STABLE_ID_TIEBREAK,
            }
        )
        for ranked in result.ranked:
            assert set(ranked.factors) <= RANKING_FACTORS
            assert len(ranked.factors) == len(set(ranked.factors))

    def test_no_provider_or_offer_fields(self):
        names = {f.name for f in dataclasses.fields(OpportunityRankingResult)}
        assert names == {
            "evaluated_at",
            "creator_id",
            "user_id",
            "ranked",
            "selected",
            "policy_version",
        }
        blob = " ".join(names).lower()
        for token in ("link", "token", "live", "offer_id", "prose", "llm", "score", "probab"):
            assert token not in blob


class TestStaticBoundary:
    def _imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        return sorted(
            {
                (n.module or "").split(".")[0]
                for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom)
            }
            | {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        )

    def test_standalone_stdlib_imports_only(self):
        assert self._imports() == ["__future__", "dataclasses", "datetime", "typing"]

    def test_no_legacy_ranking_or_heuristic_mechanics(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in (
            "rank_products_by_relevance",
            "resolve_commerce_product_with_history",
            "compute_pressure",
            "adaptive_optimization",
            "revenue_intelligence",
            "next_best_action",
            "content_matching",
            "product_selection",
            "vault_ranking",
            "conversational",
            "operational_execution",
            "bundle_group",
            "bundle_related",
            "display_group",
            "SUPPRESS_PRODUCT_FAMILY",
            "seller_earning",
            "min_relevance",
            "derive_commercial_temperature",
            "derive_desire_stage",
            "evaluate_offer_readiness",
            "derive_sales_window",
            "derive_relationship_state",
            "extract_commerce_signals",
        ):
            assert token not in src

    def test_no_io_provider_llm_redis_mechanics(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for token in (
            "get_pool",
            "fetchrow",
            "fetch(",
            "import redis",
            "get_redis",
            "from db.redis",
            "from redis",
            "get_drop",
            "create_drop",
            "adopt_drop",
            "build_checkout_url",
            "httpx",
            "requests.",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "llm_worker",
            "conversion_probability",
            "propensity_score",
            "pressure_score",
            "fatigue_score",
            "segments.",
        ):
            assert token not in src

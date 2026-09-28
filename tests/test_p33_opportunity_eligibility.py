"""P3.3.10 — pure eligibility evaluator tests (no I/O, no mocks for infra).

Covers ``commerce.opportunity_eligibility.evaluate_opportunity_eligibility``
with directly constructed candidates and ``OfferHistory`` facts: scope
gates, definition validity, ownership overlap via the authoritative
classifier, active-duplicate policy, historical-offer/family/delivery
non-exclusion, denial-reason vocabulary, determinism, input validation,
and the pure/no-provider/no-ranking production boundary.
"""

import dataclasses
import inspect
from pathlib import Path

import pytest

from commerce.offer_history import OfferHistory
from commerce.opportunity import (
    PROVIDER_VERIFICATION_UNVERIFIED,
    OpportunityCandidate,
    candidate_from_definition,
)
from commerce.opportunity_eligibility import (
    ACTIVE_DUPLICATE_OFFER,
    CREATOR_SCOPE_MISMATCH,
    DEFINITION_NOT_ACTIVE,
    DENIAL_REASONS,
    INVALID_CURRENCY,
    INVALID_OFFER_TYPE,
    INVALID_PRICE,
    INVALID_VAULT_SET,
    OWNERSHIP_FULL_OVERLAP,
    OWNERSHIP_INVALID,
    OWNERSHIP_PARTIAL_OVERLAP,
    USER_SCOPE_MISMATCH,
    EligibilityVerdict,
    evaluate_opportunity_eligibility,
)

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_eligibility.py"


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


class TestHappyPath:
    def test_zero_overlap_is_eligible(self):
        verdict = evaluate_opportunity_eligibility(_candidate(), frozenset({"V9"}), _history())
        assert verdict.eligible is True
        assert verdict.denial_reasons == ()
        assert verdict.overlap_result == "zero"
        assert verdict.provider_verification == "unverified"
        assert verdict.was_canonical_set_offered is False
        assert verdict.has_active_duplicate is False

    def test_verdict_is_frozen(self):
        assert EligibilityVerdict.__dataclass_params__.frozen is True

    def test_verdict_field_shape_has_no_ranking(self):
        names = {f.name for f in dataclasses.fields(EligibilityVerdict)}
        assert names == {
            "eligible",
            "denial_reasons",
            "overlap_result",
            "provider_verification",
            "was_canonical_set_offered",
            "has_active_duplicate",
        }


class TestScope:
    def test_creator_mismatch(self):
        candidate = candidate_from_definition(2, 10, _definition(creator_id=2))
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert CREATOR_SCOPE_MISMATCH in verdict.denial_reasons
        assert USER_SCOPE_MISMATCH not in verdict.denial_reasons

    def test_user_mismatch(self):
        candidate = candidate_from_definition(1, 11, _definition())
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert USER_SCOPE_MISMATCH in verdict.denial_reasons
        assert CREATOR_SCOPE_MISMATCH not in verdict.denial_reasons

    def test_explicit_context_is_authoritative(self):
        verdict = evaluate_opportunity_eligibility(
            _candidate(), frozenset(), _history(), creator_id=2, user_id=10
        )
        assert verdict.eligible is False
        assert CREATOR_SCOPE_MISMATCH in verdict.denial_reasons

    def test_history_scoped_elsewhere_denies(self):
        verdict = evaluate_opportunity_eligibility(
            _candidate(), frozenset(), _history(creator_id=2, user_id=10)
        )
        assert verdict.eligible is False
        assert CREATOR_SCOPE_MISMATCH in verdict.denial_reasons


class TestDefinitionValidity:
    @pytest.mark.parametrize("status", ["draft", "retired", "archived"])
    def test_inactive_definition_rejects(self, status):
        verdict = evaluate_opportunity_eligibility(
            _candidate(status=status), frozenset(), _history()
        )
        assert verdict.eligible is False
        assert DEFINITION_NOT_ACTIVE in verdict.denial_reasons

    def test_empty_status_rejects(self):
        candidate = OpportunityCandidate(
            creator_id=1,
            user_id=10,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="SINGLE",
            canonical_vault_item_ids=("V1",),
            family_id=None,
            price_minor=100,
            currency="USD",
            allow_download=True,
            definition_status="",
        )
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert DEFINITION_NOT_ACTIVE in verdict.denial_reasons

    def test_invalid_offer_type_rejects(self):
        candidate = OpportunityCandidate(
            creator_id=1,
            user_id=10,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="FLASH",
            canonical_vault_item_ids=("V1",),
            family_id=None,
            price_minor=100,
            currency="USD",
            allow_download=True,
        )
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert INVALID_OFFER_TYPE in verdict.denial_reasons

    @pytest.mark.parametrize("offer_type", ["SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"])
    def test_known_offer_types_pass_this_gate(self, offer_type):
        verdict = evaluate_opportunity_eligibility(
            _candidate(offer_type=offer_type), frozenset({"V9"}), _history()
        )
        assert INVALID_OFFER_TYPE not in verdict.denial_reasons

    @pytest.mark.parametrize(
        "vault_ids",
        [
            (),
            ("V2", "V1"),
            ("V1", "V1"),
            tuple(f"V{i}" for i in range(11)),
            ("",),
            ("V1", None),
        ],
    )
    def test_invalid_vault_set_rejects(self, vault_ids):
        candidate = OpportunityCandidate(
            creator_id=1,
            user_id=10,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="SINGLE",
            canonical_vault_item_ids=vault_ids,
            family_id=None,
            price_minor=100,
            currency="USD",
            allow_download=True,
        )
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert INVALID_VAULT_SET in verdict.denial_reasons

    @pytest.mark.parametrize("price", [-1, -500, True, "1999", 19.99, None])
    def test_invalid_price_rejects(self, price):
        candidate = OpportunityCandidate(
            creator_id=1,
            user_id=10,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="SINGLE",
            canonical_vault_item_ids=("V1",),
            family_id=None,
            price_minor=price,
            currency="USD",
            allow_download=True,
        )
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert INVALID_PRICE in verdict.denial_reasons

    def test_zero_price_passes_this_gate(self):
        verdict = evaluate_opportunity_eligibility(
            _candidate(price_minor=0, canonical_vault_item_ids=["V1"]),
            frozenset({"V9"}),
            _history(),
        )
        assert INVALID_PRICE not in verdict.denial_reasons

    @pytest.mark.parametrize("currency", ["", "EUR", "GBP", None, 123])
    def test_invalid_currency_rejects(self, currency):
        candidate = OpportunityCandidate(
            creator_id=1,
            user_id=10,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="SINGLE",
            canonical_vault_item_ids=("V1",),
            family_id=None,
            price_minor=100,
            currency=currency,
            allow_download=True,
        )
        verdict = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert verdict.eligible is False
        assert INVALID_CURRENCY in verdict.denial_reasons

    def test_lowercase_usd_normalizes_to_valid(self):
        candidate = OpportunityCandidate(
            creator_id=1,
            user_id=10,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="SINGLE",
            canonical_vault_item_ids=("V1",),
            family_id=None,
            price_minor=100,
            currency="usd",
            allow_download=True,
        )
        verdict = evaluate_opportunity_eligibility(candidate, frozenset({"V9"}), _history())
        assert INVALID_CURRENCY not in verdict.denial_reasons


class TestOwnershipOverlap:
    def test_partial_overlap_rejects_whole(self):
        verdict = evaluate_opportunity_eligibility(
            _candidate(), frozenset({"V1", "V9"}), _history()
        )
        assert verdict.eligible is False
        assert verdict.denial_reasons == (OWNERSHIP_PARTIAL_OVERLAP,)
        assert verdict.overlap_result == "partial"

    def test_full_overlap_rejects(self):
        verdict = evaluate_opportunity_eligibility(
            _candidate(), frozenset({"V1", "V2", "V3"}), _history()
        )
        assert verdict.eligible is False
        assert OWNERSHIP_FULL_OVERLAP in verdict.denial_reasons
        assert verdict.overlap_result == "full"

    def test_unusable_ownership_rejects_invalid(self):
        verdict = evaluate_opportunity_eligibility(_candidate(), [123], _history())
        assert verdict.eligible is False
        assert OWNERSHIP_INVALID in verdict.denial_reasons
        assert verdict.overlap_result == "invalid"

    def test_never_slices_partially_owned_bundle(self):
        candidate = _candidate()
        verdict = evaluate_opportunity_eligibility(candidate, frozenset({"V1"}), _history())
        assert verdict.eligible is False
        # Candidate itself is untouched (immutable, whole set preserved).
        assert candidate.canonical_vault_item_ids == ("V1", "V2")


class TestPriorOfferPolicy:
    def test_active_duplicate_rejects(self):
        history = _history(
            has_active_offer=True,
            active_offer_count=1,
            active_vault_sets=(("V1", "V2"),),
            offered_vault_sets=(("V1", "V2"),),
        )
        verdict = evaluate_opportunity_eligibility(_candidate(), frozenset({"V9"}), history)
        assert verdict.eligible is False
        assert ACTIVE_DUPLICATE_OFFER in verdict.denial_reasons
        assert verdict.has_active_duplicate is True

    def test_active_different_set_does_not_reject(self):
        history = _history(
            has_active_offer=True,
            active_offer_count=1,
            active_vault_sets=(("V7",),),
        )
        verdict = evaluate_opportunity_eligibility(_candidate(), frozenset({"V9"}), history)
        assert ACTIVE_DUPLICATE_OFFER not in verdict.denial_reasons
        assert verdict.eligible is True

    def test_active_without_snapshot_identity_cannot_duplicate(self):
        history = _history(
            has_active_offer=True,
            active_offer_count=1,
            active_vault_sets=(),
            null_snapshot_count=1,
        )
        verdict = evaluate_opportunity_eligibility(_candidate(), frozenset({"V9"}), history)
        assert ACTIVE_DUPLICATE_OFFER not in verdict.denial_reasons
        assert verdict.eligible is True

    def test_historical_offer_does_not_reject(self):
        history = _history(
            total_offer_count=3,
            recent_offer_count=1,
            offered_vault_sets=(("V1", "V2"),),
        )
        verdict = evaluate_opportunity_eligibility(_candidate(), frozenset({"V9"}), history)
        assert verdict.eligible is True
        assert verdict.was_canonical_set_offered is True
        assert verdict.has_active_duplicate is False

    def test_family_membership_never_excludes(self):
        verdict = evaluate_opportunity_eligibility(
            _candidate(family_id=42), frozenset({"V9"}), _history()
        )
        assert verdict.eligible is True

    def test_delivery_is_not_an_evaluator_input(self):
        params = inspect.signature(evaluate_opportunity_eligibility).parameters
        for forbidden in (
            "delivered",
            "delivery",
            "segment",
            "conversation",
            "tone",
            "llm",
            "pressure",
            "fatigue",
            "score",
        ):
            assert forbidden not in params
            assert not any(forbidden in name for name in params)


class TestDenialVocabulary:
    def test_all_required_reasons_exist(self):
        assert DENIAL_REASONS == frozenset(
            {
                CREATOR_SCOPE_MISMATCH,
                USER_SCOPE_MISMATCH,
                DEFINITION_NOT_ACTIVE,
                INVALID_OFFER_TYPE,
                INVALID_VAULT_SET,
                OWNERSHIP_FULL_OVERLAP,
                OWNERSHIP_PARTIAL_OVERLAP,
                OWNERSHIP_INVALID,
                INVALID_PRICE,
                INVALID_CURRENCY,
                ACTIVE_DUPLICATE_OFFER,
            }
        )

    def test_no_ranking_reasons(self):
        blob = " ".join(sorted(DENIAL_REASONS)).lower()
        for token in (
            "relevance",
            "propensity",
            "conversion",
            "fatigue",
            "pressure",
            "conversation",
            "aov",
            "spend",
        ):
            assert token not in blob

    def test_verdict_reasons_are_deterministic_order(self):
        candidate = OpportunityCandidate(
            creator_id=9,
            user_id=99,
            definition_id=11,
            stable_key="k",
            version=1,
            offer_type="FLASH",
            canonical_vault_item_ids=(),
            family_id=None,
            price_minor=-1,
            currency="EUR",
            allow_download=True,
            definition_status="retired",
        )
        first = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        second = evaluate_opportunity_eligibility(candidate, frozenset(), _history())
        assert first == second
        assert first.denial_reasons == (
            CREATOR_SCOPE_MISMATCH,
            USER_SCOPE_MISMATCH,
            DEFINITION_NOT_ACTIVE,
            INVALID_OFFER_TYPE,
            INVALID_VAULT_SET,
            INVALID_PRICE,
            INVALID_CURRENCY,
            OWNERSHIP_INVALID,
        )
        assert first.eligible is False


class TestInputValidation:
    def test_missing_candidate_raises(self):
        with pytest.raises(TypeError):
            evaluate_opportunity_eligibility(None, frozenset(), _history())

    def test_missing_history_raises(self):
        with pytest.raises(TypeError):
            evaluate_opportunity_eligibility(_candidate(), frozenset(), None)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"creator_id": 0},
            {"creator_id": -3},
            {"user_id": 0},
            {"user_id": True},
        ],
    )
    def test_invalid_context_raises(self, kwargs):
        with pytest.raises(ValueError):
            evaluate_opportunity_eligibility(_candidate(), frozenset(), _history(), **kwargs)


class TestPurityBoundary:
    def test_no_io_provider_ranking_mechanics(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        # Mechanics assertions (prose docstrings may name exclusions in words).
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
            "attach_drop",
            "check_drop",
            "httpx",
            "requests.",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "product_selection",
            "content_matching",
            "vault_ranking",
            "conversational",
            "operational_execution",
            "taxonomy",
            "bundle_group",
            "llm_worker",
            "conversion_probability",
            "pressure_score",
            "fatigue",
            "novelty",
            "segments.",
        ):
            assert token not in src

    def test_uses_authoritative_overlap_primitive(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "classify_vault_overlap" in src
        assert "OverlapResult" in src
        assert "commerce.ownership" in src

    def test_provider_slot_matches_candidate_contract(self):
        from commerce.opportunity_eligibility import (
            PROVIDER_VERIFICATION_UNVERIFIED as ELIG_UNVERIFIED,
        )

        assert ELIG_UNVERIFIED == PROVIDER_VERIFICATION_UNVERIFIED == "unverified"
        verdict = evaluate_opportunity_eligibility(_candidate(), frozenset(), _history())
        assert verdict.provider_verification == PROVIDER_VERIFICATION_UNVERIFIED

"""P3.3.10 — immutable opportunity candidate tests (pure, no I/O).

Covers ``commerce.opportunity``: exact immutable field shape, canonical
Vault identity, tuple/frozenset immutability, absence of
score/probability/LLM fields, neutral provider state, deterministic
construction from definition rows, and fail-closed malformed input.
"""

import dataclasses
from pathlib import Path

import pytest

from commerce.opportunity import (
    OVERLAP_UNKNOWN,
    PROVIDER_VERIFICATION_UNVERIFIED,
    CandidatePriorOfferFacts,
    OpportunityCandidate,
    candidate_from_definition,
)

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "opportunity.py"


def _definition(**over):
    row = {
        "id": 11,
        "creator_id": 1,
        "stable_key": "black-lingerie",
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


class TestFieldShape:
    def test_exact_immutable_field_shape(self):
        fields = {f.name for f in dataclasses.fields(OpportunityCandidate)}
        assert fields == {
            "creator_id",
            "user_id",
            "definition_id",
            "stable_key",
            "version",
            "offer_type",
            "canonical_vault_item_ids",
            "family_id",
            "price_minor",
            "currency",
            "allow_download",
            "mapped_drop_ids",
            "definition_status",
            "overlap_result",
            "denial_reason",
            "prior_offer_facts",
            "provider_verification",
        }

    def test_frozen_immutable(self):
        assert OpportunityCandidate.__dataclass_params__.frozen is True
        candidate = candidate_from_definition(1, 10, _definition())
        with pytest.raises(dataclasses.FrozenInstanceError):
            candidate.price_minor = 1  # type: ignore[misc]

    def test_no_score_probability_llm_fields(self):
        names = {f.name for f in dataclasses.fields(OpportunityCandidate)}
        blob = " ".join(names).lower()
        for token in (
            "score",
            "probab",
            "pressure",
            "fatigue",
            "convers",
            "prose",
            "recommend",
            "llm",
            "segment",
            "novelty",
            "propensity",
            "aov",
            "spend",
        ):
            assert token not in blob

    def test_prior_facts_shape(self):
        fields = {f.name for f in dataclasses.fields(CandidatePriorOfferFacts)}
        assert fields == {
            "was_canonical_set_offered",
            "has_active_duplicate",
            "definition_history_available",
        }
        assert CandidatePriorOfferFacts.__dataclass_params__.frozen is True


class TestCanonicalVaultIds:
    def test_canonical_ids_preserved_as_tuple(self):
        candidate = candidate_from_definition(
            1, 10, _definition(canonical_vault_item_ids=["V1", "V2"])
        )
        assert candidate.canonical_vault_item_ids == ("V1", "V2")
        assert isinstance(candidate.canonical_vault_item_ids, tuple)

    def test_mapped_drops_sorted_deduped_tuple(self):
        candidate = candidate_from_definition(
            1, 10, _definition(), mapped_drop_ids=["drop_b", "drop_a", "drop_a"]
        )
        assert candidate.mapped_drop_ids == ("drop_a", "drop_b")
        assert isinstance(candidate.mapped_drop_ids, tuple)

    @pytest.mark.parametrize(
        "bad_ids",
        [
            [],
            ["V2", "V1"],
            ["V1", "V1"],
            [f"V{i}" for i in range(11)],
            ["  "],
            [None],
            "V1",
        ],
    )
    def test_noncanonical_vault_sets_raise(self, bad_ids):
        with pytest.raises(ValueError):
            candidate_from_definition(1, 10, _definition(canonical_vault_item_ids=bad_ids))


class TestProviderNeutrality:
    def test_provider_state_starts_unverified(self):
        candidate = candidate_from_definition(1, 10, _definition())
        assert candidate.provider_verification == "unverified"
        assert PROVIDER_VERIFICATION_UNVERIFIED == "unverified"

    def test_no_verification_timestamp_or_live_price(self):
        names = {f.name for f in dataclasses.fields(OpportunityCandidate)}
        blob = " ".join(names).lower()
        assert "timestamp" not in blob
        assert "live" not in blob
        assert "verified_at" not in blob
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "verified_at" not in src
        assert "live_price" not in src


class TestConstruction:
    def test_identity_and_offer_facts_verbatim(self):
        candidate = candidate_from_definition(
            1, 10, _definition(family_id=4), mapped_drop_ids=["drop_x"]
        )
        assert (candidate.creator_id, candidate.user_id) == (1, 10)
        assert (candidate.definition_id, candidate.stable_key, candidate.version) == (
            11,
            "black-lingerie",
            1,
        )
        assert candidate.offer_type == "SINGLE"
        assert candidate.family_id == 4
        assert candidate.price_minor == 1999
        assert candidate.currency == "USD"
        assert candidate.allow_download is True
        assert candidate.mapped_drop_ids == ("drop_x",)
        assert candidate.definition_status == "active"

    def test_offer_type_uppercased_status_lowercased(self):
        candidate = candidate_from_definition(
            1, 10, _definition(offer_type="core_bundle", status="Active")
        )
        assert candidate.offer_type == "CORE_BUNDLE"
        assert candidate.definition_status == "active"

    def test_eligibility_slots_start_neutral(self):
        candidate = candidate_from_definition(1, 10, _definition())
        assert candidate.overlap_result == OVERLAP_UNKNOWN == "unknown"
        assert candidate.denial_reason is None
        assert candidate.prior_offer_facts is None

    def test_invalid_price_and_currency_carried_for_evaluator(self):
        candidate = candidate_from_definition(
            1, 10, _definition(price_minor=-5, currency="EUR", status="retired")
        )
        assert candidate.price_minor == -5
        assert candidate.currency == "EUR"
        assert candidate.definition_status == "retired"

    def test_definition_creator_mismatch_raises(self):
        with pytest.raises(ValueError, match="creator"):
            candidate_from_definition(2, 10, _definition())

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"id": None},
            {"stable_key": "  "},
            {"version": 0},
            {"offer_type": ""},
            {"currency": None},
            {"family_id": "x"},
        ],
    )
    def test_malformed_rows_raise_never_repaired(self, kwargs):
        with pytest.raises(ValueError):
            candidate_from_definition(1, 10, _definition(**kwargs))

    def test_deterministic_repeated_construction(self):
        first = candidate_from_definition(1, 10, _definition(), mapped_drop_ids=["d1"])
        second = candidate_from_definition(1, 10, _definition(), mapped_drop_ids=["d1"])
        assert first == second
        assert hash(first) == hash(second)

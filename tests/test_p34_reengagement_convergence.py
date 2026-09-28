"""P3.4 — scheduler/re-engagement convergence (unit, no DB/provider/Redis writes).

Proves the scheduler re-engagement sweep no longer references a commercial
product without consulting the existing Opportunity Engine eligibility
authority:

    stale pending offer
      -> commerce.reengagement_eligibility.is_stale_offer_reengageable
         (get_offer + canonical Vault identity + OfferDefinition validity
          + ownership overlap + other-active duplicate, all fail-closed)
      -> existing create_scheduled_message() with the deterministic generic
         content contract and preserved dedup identity.

No new offer, price, product, ranking, sealing, execution, LLM, provider, or
legacy commerce authority anywhere on the path.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

REENGAGEMENT_PATH = Path(__file__).parent.parent / "commerce" / "re_engagement.py"
SCHEDULER_PATH = Path(__file__).parent.parent / "workers" / "scheduler_worker.py"
ADAPTER_PATH = Path(__file__).parent.parent / "commerce" / "reengagement_eligibility.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sealed_reason(definition_id=11, version=2, stable_key="alpha"):
    return json.dumps(
        {
            "v": 1,
            "definition_id": definition_id,
            "definition_version": version,
            "stable_key": stable_key,
            "sealed_at": "2026-01-01T00:00:00+00:00",
            "verified_at": "2026-01-01T00:00:00+00:00",
            "verified_hash": "h",
            "allow_download": True,
            "verifier_version": 1,
        },
        sort_keys=True,
    )


def _stale_offer(**over):
    base = {
        "id": 42,
        "creator_id": 1,
        "user_id": 10,
        "product_id": 77,
        "dropfans_product_id": "drop_x",
        "state": "pending",
        "vault_item_ids": ["V1", "V2"],
        "price_minor": 1999,
        "currency": "USD",
        "reason": _sealed_reason(),
        "created_at": datetime.now(timezone.utc) - timedelta(hours=72),
    }
    base.update(over)
    return base


def _definition(**over):
    base = {
        "id": 11,
        "creator_id": 1,
        "stable_key": "alpha",
        "version": 2,
        "offer_type": "SMALL_BUNDLE",
        "canonical_vault_item_ids": ["V1", "V2"],
        "family_id": None,
        "price_minor": 1999,
        "currency": "USD",
        "allow_download": True,
        "status": "active",
    }
    base.update(over)
    return base


def _patch_eligibility_stack(
    monkeypatch,
    *,
    fresh=None,
    definition=None,
    owned=frozenset(),
    actives=(),
    definition_raises=None,
    offer_raises=None,
    owned_raises=None,
    actives_raises=None,
):
    """Patch the four store reads behind is_stale_offer_reengageable."""
    fresh_row = fresh if fresh is not None else _stale_offer()
    # definition=None means "default active definition"; pass definition=False
    # to simulate a missing row (None from the store).
    definition_row = _definition() if definition is None else (None if definition is False else definition)

    async def _get_offer(creator_id, offer_id):
        if offer_raises is not None:
            raise offer_raises
        return fresh_row

    async def _get_definition(creator_id, definition_id, version=None):
        if definition_raises is not None:
            raise definition_raises
        return definition_row

    async def _owned(creator_id, user_id):
        if owned_raises is not None:
            raise owned_raises
        return owned

    async def _actives(user_id, creator_id=None, limit=100, **kw):
        if actives_raises is not None:
            raise actives_raises
        return list(actives)

    monkeypatch.setattr("commerce.dao.get_offer", _get_offer)
    monkeypatch.setattr("db.offer_definitions.get_offer_definition", _get_definition)
    monkeypatch.setattr("commerce.ownership.fetch_owned_vault_ids", _owned)
    monkeypatch.setattr("commerce.dao.list_offers_for_user", _actives)
    return {
        "fresh": fresh_row,
        "definition": None if definition is False else (definition if definition is not None else _definition()),
    }


class _FakeConn:
    def __init__(self, created_at):
        self._created_at = created_at

    async def fetchrow(self, *a, **k):
        return {"created_at": self._created_at}

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_schedule_stack(monkeypatch, *, eligible=True, reason="eligible", created_hours_ago=72):
    """Patch everything behind schedule_reengagement_if_eligible."""
    monkeypatch.setattr(
        "commerce.reengagement_eligibility.is_stale_offer_reengageable",
        AsyncMock(return_value=(eligible, reason)),
    )
    monkeypatch.setattr(
        "commerce.dao.get_timing_context",
        AsyncMock(return_value={"hours_since_last_offer": 72.0, "recent_offer_count": 1}),
    )
    monkeypatch.setattr(
        "commerce.dao.get_behavioral_feedback_context",
        AsyncMock(return_value={"consecutive_rejections": 0, "aftercare_status": "none"}),
    )
    created = datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)
    fake_conn = _FakeConn(created)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=fake_conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    monkeypatch.setattr("db.postgres.get_pool", AsyncMock(return_value=pool))
    created_msg = AsyncMock(return_value=101)
    monkeypatch.setattr("db.postgres.create_scheduled_message", created_msg)
    return created_msg


def _explode_legacy(monkeypatch):
    paths = [
        "commerce.product_selection.resolve_commerce_product_with_history",
        "commerce.product_selection.list_valid_products",
        "commerce.decision.decide_from_signals",
        "commerce.strategy.build_strategy",
        "commerce.orchestrator.orchestrate_commerce",
        "commerce.execution.execute_ppv",
        "commerce.dao.create_offer_serialized",
        "commerce.opportunity_sealing.seal_ranked_candidate",
        "commerce.opportunity_execution.execute_sealed_offer",
        "commerce.opportunity_ranking.rank_candidates",
    ]
    for p in paths:
        monkeypatch.setattr(p, AsyncMock(side_effect=AssertionError(f"legacy called: {p}")), raising=False)


# ---------------------------------------------------------------------------
# 1-8: eligibility + scheduling behaviour
# ---------------------------------------------------------------------------


class TestEligibilityVerdicts:
    @pytest.mark.asyncio
    async def test_eligible(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch)
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert (ok, reason) == (True, "eligible")

    @pytest.mark.asyncio
    async def test_owned_full_skips(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, owned=frozenset({"V1", "V2"}))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False and reason == "owned_full"

    @pytest.mark.asyncio
    async def test_partial_ownership_follows_overlap_policy(self, monkeypatch):
        # Existing Opportunity policy: PARTIAL overlap rejects whole, never sliced.
        _patch_eligibility_stack(monkeypatch, owned=frozenset({"V1"}))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False and reason == "owned_partial"

    @pytest.mark.asyncio
    async def test_active_duplicate_skips(self, monkeypatch):
        other = _stale_offer(id=43, state="pending")  # same canonical set, other id
        _patch_eligibility_stack(monkeypatch, actives=[other])
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False and reason == "active_duplicate"

    @pytest.mark.asyncio
    async def test_active_duplicate_ignores_subject_itself(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, actives=[_stale_offer()])
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, _ = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is True

    @pytest.mark.asyncio
    async def test_invalid_creator_scope_skips_without_store(self, monkeypatch):
        called = []

        async def _boom(*a, **k):
            called.append(1)
            raise AssertionError("store must not be touched on invalid scope")

        monkeypatch.setattr("commerce.dao.get_offer", _boom)
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=0, user_id=10, offer=_stale_offer())
        assert (ok, reason) == (False, "invalid_scope")
        assert called == []

    @pytest.mark.asyncio
    async def test_creator_isolation_mismatch_skips(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch)
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(
            creator_id=2, user_id=10, offer=_stale_offer()
        )
        assert (ok, reason) == (False, "invalid_offer")

    @pytest.mark.asyncio
    async def test_malformed_candidate_skips(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch)
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        assert (await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=None))[0] is False
        assert (await is_stale_offer_reengageable(creator_id=1, user_id=10, offer={}))[0] is False
        # Snapshot identity comes from the fresh store row, not the sweep copy.
        _patch_eligibility_stack(monkeypatch, fresh=_stale_offer(vault_item_ids=None))
        assert (
            await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        )[1] == "invalid_snapshot"
        # Legacy rows without a sealed reason envelope cannot be mapped.
        _patch_eligibility_stack(
            monkeypatch, fresh=_stale_offer(reason="legacy free text")
        )
        assert (
            await is_stale_offer_reengageable(
                creator_id=1, user_id=10, offer=_stale_offer(reason="legacy free text")
            )
        )[1] == "definition_unresolvable"

    @pytest.mark.asyncio
    async def test_dao_failure_fails_closed(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, offer_raises=RuntimeError("db down"))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False and reason == "store_unavailable"

    @pytest.mark.asyncio
    async def test_definition_lookup_failure_fails_closed(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, definition_raises=RuntimeError("db down"))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, _ = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False

    @pytest.mark.asyncio
    async def test_ownership_failure_fails_closed(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, owned_raises=RuntimeError("db down"))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False and reason == "ownership_unavailable"

    @pytest.mark.asyncio
    async def test_offer_not_active_skips(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, fresh=_stale_offer(state="purchased"))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert (ok, reason) == (False, "offer_not_active")

    @pytest.mark.asyncio
    async def test_retired_definition_skips(self, monkeypatch):
        _patch_eligibility_stack(monkeypatch, definition=_definition(status="retired"))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, reason = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert (ok, reason) == (False, "definition_not_active")

    @pytest.mark.asyncio
    async def test_eligibility_failure_never_touches_legacy(self, monkeypatch):
        _explode_legacy(monkeypatch)
        _patch_eligibility_stack(monkeypatch, owned=frozenset({"V1", "V2"}))
        from commerce.reengagement_eligibility import is_stale_offer_reengageable

        ok, _ = await is_stale_offer_reengageable(creator_id=1, user_id=10, offer=_stale_offer())
        assert ok is False


class TestScheduleGate:
    @pytest.mark.asyncio
    async def test_stale_eligible_schedules_with_generic_contract(self, monkeypatch):
        create_msg = _patch_schedule_stack(monkeypatch, eligible=True)
        from commerce.re_engagement import schedule_reengagement_if_eligible

        assert await schedule_reengagement_if_eligible(
            creator_id=1, user_id=10, product_id=77, offer=_stale_offer()
        ) is True
        assert create_msg.await_count == 1
        kwargs = create_msg.call_args.kwargs
        assert kwargs["dedup_key"] == "reengage:1:10:77"
        assert kwargs["content"] == "Hey, still thinking about that set? 😏"
        assert kwargs["reason"] == "reengagement"
        assert kwargs["creator_id"] == 1 and kwargs["user_id"] == 10
        # No price/URL/product substitution in the deterministic content.
        assert "1999" not in kwargs["content"] and "http" not in kwargs["content"]

    @pytest.mark.asyncio
    async def test_missing_offer_identity_skips_fail_closed(self, monkeypatch):
        create_msg = _patch_schedule_stack(monkeypatch, eligible=True)
        from commerce.re_engagement import schedule_reengagement_if_eligible

        assert await schedule_reengagement_if_eligible(creator_id=1, user_id=10, product_id=77) is False
        assert create_msg.await_count == 0

    @pytest.mark.asyncio
    async def test_ineligible_offer_never_schedules(self, monkeypatch):
        create_msg = _patch_schedule_stack(monkeypatch, eligible=False, reason="owned_full")
        from commerce.re_engagement import schedule_reengagement_if_eligible

        assert await schedule_reengagement_if_eligible(
            creator_id=1, user_id=10, product_id=77, offer=_stale_offer()
        ) is False
        assert create_msg.await_count == 0

    @pytest.mark.asyncio
    async def test_eligibility_exception_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            "commerce.reengagement_eligibility.is_stale_offer_reengageable",
            AsyncMock(side_effect=RuntimeError("store down")),
        )
        create_msg = AsyncMock(return_value=1)
        monkeypatch.setattr("db.postgres.create_scheduled_message", create_msg)
        from commerce.re_engagement import schedule_reengagement_if_eligible

        assert await schedule_reengagement_if_eligible(
            creator_id=1, user_id=10, product_id=77, offer=_stale_offer()
        ) is False
        assert create_msg.await_count == 0

    @pytest.mark.asyncio
    async def test_ineligible_never_touches_legacy_or_provider(self, monkeypatch):
        _explode_legacy(monkeypatch)
        monkeypatch.setattr(
            "integrations.dropfans.service.get_drop",
            AsyncMock(side_effect=AssertionError("provider called")),
            raising=False,
        )
        create_msg = _patch_schedule_stack(monkeypatch, eligible=False, reason="active_duplicate")
        from commerce.re_engagement import schedule_reengagement_if_eligible

        assert await schedule_reengagement_if_eligible(
            creator_id=1, user_id=10, product_id=77, offer=_stale_offer()
        ) is False
        assert create_msg.await_count == 0

    @pytest.mark.asyncio
    async def test_eligible_never_creates_offer_seals_or_ranks(self, monkeypatch):
        _explode_legacy(monkeypatch)
        create_msg = _patch_schedule_stack(monkeypatch, eligible=True)
        from commerce.re_engagement import schedule_reengagement_if_eligible

        assert await schedule_reengagement_if_eligible(
            creator_id=1, user_id=10, product_id=77, offer=_stale_offer()
        ) is True
        assert create_msg.await_count == 1

    @pytest.mark.asyncio
    async def test_product_id_preserved_verbatim_for_dedup(self, monkeypatch):
        # Even if the stale row and a hypothetical selector disagree, the
        # stale offer's product_id is the only identity used (dedup only).
        create_msg = _patch_schedule_stack(monkeypatch, eligible=True)
        from commerce.re_engagement import schedule_reengagement_if_eligible

        await schedule_reengagement_if_eligible(
            creator_id=1, user_id=10, product_id=77, offer=_stale_offer(product_id=77)
        )
        assert create_msg.call_args.kwargs["dedup_key"] == "reengage:1:10:77"

    @pytest.mark.asyncio
    async def test_stale_age_gate_remains_enforced(self, monkeypatch):
        from commerce.re_engagement import is_reengagement_eligible

        ok, reason = await is_reengagement_eligible(
            creator_id=1,
            user_id=10,
            has_active_offer=True,
            offer_age_hours=12.0,
            aftercare_status="none",
            is_on_cooldown=False,
            consecutive_rejections=0,
            has_relevant_unpurchased=True,
            relationship_state="warm",
        )
        assert (ok, reason) == (False, "too_soon")

    @pytest.mark.asyncio
    async def test_existing_pressure_fatigue_gates_remain(self, monkeypatch):
        from commerce.re_engagement import is_reengagement_eligible

        for kwargs, reason in [
            ({"is_on_cooldown": True}, "cooldown"),
            ({"consecutive_rejections": 3}, "rejected"),
            ({"aftercare_status": "pending"}, "aftercare"),
        ]:
            ok, got = await is_reengagement_eligible(
                creator_id=1,
                user_id=10,
                has_active_offer=True,
                offer_age_hours=72.0,
                aftercare_status=kwargs.get("aftercare_status", "none"),
                is_on_cooldown=kwargs.get("is_on_cooldown", False),
                consecutive_rejections=kwargs.get("consecutive_rejections", 0),
                has_relevant_unpurchased=True,
                relationship_state="warm",
            )
            assert ok is False and got == reason


# ---------------------------------------------------------------------------
# Static authority audit: scheduler path shape + quarantine
# ---------------------------------------------------------------------------


class TestStaticConvergence:
    def test_scheduler_passes_stale_offer_identity(self):
        src = SCHEDULER_PATH.read_text(encoding="utf-8")
        assert "schedule_reengagement_if_eligible" in src
        assert "offer=offer" in src

    def test_scheduler_preserves_governance_gates(self):
        src = SCHEDULER_PATH.read_text(encoding="utf-8")
        for token in [
            "is_reengagement_paused",
            "is_commerce_paused",
            "is_global_paused",
            "is_reengagement_governed_allowed",
            "compute_pressure",
            "compute_fatigue",
            "age_h >= 48",
        ]:
            assert token in src, f"scheduler governance gate missing: {token}"

    def test_scheduler_dedup_identity_unchanged(self):
        src = REENGAGEMENT_PATH.read_text(encoding="utf-8")
        assert 'dedup_key = f"reengage:{creator_id}:{user_id}:{product_id}"' in src

    def test_message_content_contract_unchanged(self):
        src = REENGAGEMENT_PATH.read_text(encoding="utf-8")
        assert 'content = "Hey, still thinking about that set?' in src

    def test_no_legacy_commerce_on_modified_path(self):
        for path in (REENGAGEMENT_PATH, SCHEDULER_PATH, ADAPTER_PATH):
            src = path.read_text(encoding="utf-8")
            for token in [
                "resolve_commerce_product_with_history",
                "decide_from_signals",
                "build_strategy",
                "orchestrate_commerce",
                "execution.execute_ppv",
                "dao.create_offer_serialized",
                "product_selection",
                "seal_ranked_candidate",
                "execute_sealed_offer",
            ]:
                assert token not in src, f"legacy reference {token} in {path.name}"

    def test_no_ranking_or_sealing_on_reengagement_path(self):
        for path in (REENGAGEMENT_PATH, ADAPTER_PATH):
            src = path.read_text(encoding="utf-8")
            for token in ["rank_candidates", "evaluate_opportunity(", "seal_ranked_candidate", "get_drop("]:
                assert token not in src, f"ranking/sealing reference {token} in {path.name}"

    def test_no_llm_taxonomy_segment_earnings_authority(self):
        # Assert on import/call-shaped authority references (bare substrings
        # false-positive on prose comments such as "never invent ... via LLM").
        for path in (REENGAGEMENT_PATH, ADAPTER_PATH):
            src = path.read_text(encoding="utf-8")
            lowered = src.lower()
            for token in [
                "from core.llm",
                "llm_provider",
                "gemini_client",
                "import openai",
                "vault_taxonomy",
                "from segments",
                "from db.segments",
                "fan_segments",
                "rank_products_by_relevance",
                "list_valid_products",
                "seller_earning",
            ]:
                assert token not in lowered, f"non-deterministic authority {token} in {path.name}"

    def test_no_feature_flag_or_parallel_selector(self):
        for path in (REENGAGEMENT_PATH, SCHEDULER_PATH, ADAPTER_PATH):
            src = path.read_text(encoding="utf-8")
            for token in [
                "USE_OPPORTUNITY_ENGINE",
                "ENABLE_NEW_COMMERCE",
                "LEGACY_PPV_ENABLED",
                "opportunity_engine_enabled",
                "P34_ENABLED",
                "REENGAGEMENT_V2",
            ]:
                assert token not in src

    def test_adapter_reuses_existing_authority(self):
        src = ADAPTER_PATH.read_text(encoding="utf-8")
        for token in [
            "commerce.dao import get_offer",
            "canonical_identity_ids",
            "get_offer_definition",
            "fetch_owned_vault_ids",
            "classify_vault_overlap",
            "list_offers_for_user",
        ]:
            assert token in src, f"adapter must reuse existing authority: {token}"

    def test_no_provider_mutation_or_offer_creation_in_adapter(self):
        src = ADAPTER_PATH.read_text(encoding="utf-8")
        for token in [
            "INSERT INTO commerce_offers",
            "create_offer",
            "post(",
            "get_drop(",
            "buy_url",
            "seal_ranked_candidate",
            "execute_sealed_offer",
        ]:
            assert token not in src

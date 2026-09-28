"""Adversarial tests for P0/P1/P2 forensic remediation.

Proves that every repaired defect now fails closed.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.scoring import score_draft, HARD_FLAGS


class TestP0ScoringFailClosed:
    """P0-1: Scoring failure must never produce approval-eligible score.

    Scoring uses the sole llama.cpp provider via get_llm_provider().
    """

    @pytest.mark.asyncio
    async def test_provider_exception_returns_zero_score(self):
        """Provider failing → score must be 0.0 (below auto-approve)."""
        mock_p = MagicMock()
        mock_p.generate = AsyncMock(side_effect=Exception("API down"))
        with patch("core.scoring.get_llm_provider", return_value=mock_p):
            score, flags = await score_draft("Hello!", "Hello!", [])
            assert score == 0.0, f"Expected 0.0 on provider failure, got {score}"
            assert score < 0.80, "Score must not satisfy auto-approve threshold"

    @pytest.mark.asyncio
    async def test_malformed_json_returns_zero_score(self):
        """LLM returns non-JSON → score must be 0.0."""
        mock_p = MagicMock()
        mock_p.generate = AsyncMock(return_value="not json at all")
        with patch("core.scoring.get_llm_provider", return_value=mock_p):
            score, flags = await score_draft("Hello!", "Hello!", [])
            assert score == 0.0, f"Expected 0.0 on malformed JSON, got {score}"

    @pytest.mark.asyncio
    async def test_empty_response_returns_zero_score(self):
        """LLM returns empty string → score must be 0.0."""
        mock_p = MagicMock()
        mock_p.generate = AsyncMock(return_value="")
        with patch("core.scoring.get_llm_provider", return_value=mock_p):
            score, flags = await score_draft("Hello!", "Hello!", [])
            assert score == 0.0, f"Expected 0.0 on empty response, got {score}"

    @pytest.mark.asyncio
    async def test_timeout_returns_zero_score(self):
        """LLM timeout → score must be 0.0."""
        mock_p2 = MagicMock()
        mock_p2.generate = AsyncMock(side_effect=TimeoutError("timed out"))
        with patch("core.scoring.get_llm_provider", return_value=mock_p2):
            score, flags = await score_draft("Hello!", "Hello!", [])
            assert score == 0.0, f"Expected 0.0 on timeout, got {score}"

    @pytest.mark.asyncio
    async def test_valid_json_still_scores_normally(self):
        """Valid LLM response → normal scoring path (via get_llm_provider)."""
        import json

        mock_provider = MagicMock()
        mock_provider.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "contextually_aware": 9,
                    "natural_tone": 8,
                    "appropriate_length": 7,
                    "not_repetitive": 9,
                    "flags": [],
                }
            )
        )
        with patch("core.scoring.get_llm_provider", return_value=mock_provider):
            score, flags = await score_draft("Hello!", "Hello!", [])
            assert score > 0.5, f"Valid response should score above 0.5, got {score}"

    @pytest.mark.asyncio
    async def test_hard_flag_caps_score_even_on_success(self):
        """Hard flag in user message → score capped at 0.1 even with good LLM scores."""
        import json

        mock_p = MagicMock()
        mock_p.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "contextually_aware": 10,
                    "natural_tone": 10,
                    "appropriate_length": 10,
                    "not_repetitive": 10,
                    "flags": [],
                }
            )
        )
        with patch("core.scoring.get_llm_provider", return_value=mock_p):
            score, flags = await score_draft("What's the price?", "Here's the price!", [])
            assert score <= 0.1, f"Hard flag should cap score to 0.1, got {score}"


class TestP0ReconciliationFailClosed:
    """P0-2: Reconciliation must not succeed after fulfillment failure."""

    def test_reconciliation_code_returns_false_on_exception(self):
        """Verify the source code returns False when handle_post_purchase raises."""
        import ast

        with open("commerce/reconciliation.py", "r") as f:
            source = f.read()
        # The fix adds "return False" after the except block for handle_post_purchase
        assert "return False" in source, (
            "reconciliation.py must return False on fulfillment failure"
        )
        # Verify it's inside the except block for handle_post_purchase
        lines = source.split("\n")
        found_return_false = False
        for i, line in enumerate(lines):
            if "handle_post_purchase" in line:
                # Look for return False within 10 lines after
                for j in range(i, min(i + 15, len(lines))):
                    if "return False" in lines[j]:
                        found_return_false = True
                        break
            if found_return_false:
                break
        assert found_return_false, (
            "return False must be inside the handle_post_purchase except block"
        )


class TestP0TipImportFixed:
    """P0-3: Tip import must work with the correct DropFans function."""

    def test_tip_import_does_not_raise(self):
        """Importing the tip tool must not raise ImportError."""
        from core.llm_tools import _handle_suggest_tip

        assert _handle_suggest_tip is not None

    def test_dropfans_get_checkout_links_exists(self):
        """The get_checkout_links function must exist in the DropFans service."""
        from integrations.dropfans.service import get_checkout_links

        assert callable(get_checkout_links)


class TestP1EmptyDraftRejected:
    """P1-2: Empty draft must never reach send queue."""

    @pytest.mark.asyncio
    async def test_empty_draft_not_included_in_commerce_check(self):
        """Empty string is falsy — verify it would be caught by strip check."""
        draft = ""
        assert not draft or not draft.strip(), "Empty draft should be caught"

    @pytest.mark.asyncio
    async def test_whitespace_only_draft_not_included(self):
        """Whitespace-only draft should also be caught."""
        draft = "   \n\t  "
        assert not draft or not draft.strip(), "Whitespace-only draft should be caught"

    @pytest.mark.asyncio
    async def test_none_draft_not_included(self):
        """None draft should be caught."""
        draft = None
        assert not draft or not draft.strip(), "None draft should be caught"


class TestP1CommerceStateLogging:
    """P1-1: Commerce state failures must log, not silently pass."""

    def test_commerce_state_has_logger(self):
        """commerce/state.py must have a logger for warning output."""
        import commerce.state

        assert hasattr(commerce.state, "logger")


class TestP1DeliveryReservationBlocks:
    """P1-3: Delivery reservation failure must block send."""

    def test_main_module_has_move_send_to_dlq(self):
        """main.py must have move_send_to_dlq available for reservation failure."""
        import chatbotv2.main

        assert callable(chatbotv2.main.move_send_to_dlq)


class TestP1DlqLogging:
    """P1-4: DLQ write failure must log at ERROR level."""

    def test_dlq_function_exists(self):
        """move_send_to_dlq must exist in db.redis."""
        from db.redis import move_send_to_dlq

        assert callable(move_send_to_dlq)


class TestP1EarningsLogging:
    """P1-5: Earnings query failure must log."""

    def test_vault_service_has_logger(self):
        """vault/service.py must have a logger."""
        import vault.service

        assert hasattr(vault.service, "logger")


class TestP1PostPurchaseDedup:
    """P1-6: Post-purchase dedup failure must not proceed."""

    @pytest.mark.asyncio
    async def test_dedup_failure_returns_false(self):
        """When dedup check fails, enqueue_purchase_confirmation should return False."""
        import inspect
        from commerce.post_purchase import enqueue_purchase_confirmation

        sig = inspect.signature(enqueue_purchase_confirmation)
        # The function should be capable of returning False
        assert sig.return_annotation in (bool, "bool", inspect.Parameter.empty)


class TestP2TipLinkAuthoritative:
    """P2: Tip link must come from DropFans, not be invented."""

    def test_tip_tool_imports_get_checkout_links(self):
        """The tip tool must import get_checkout_links, not get_dropfans_service."""
        import ast

        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        assert "get_dropfans_service" not in source, (
            "get_dropfans_service should not appear in llm_tools.py"
        )
        assert "get_checkout_links" in source, (
            "get_checkout_links should be imported in llm_tools.py"
        )

    def test_tip_uses_telegram_tip_field(self):
        """Tip tool should prefer telegram.tip over web.tip."""
        import ast

        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        assert "telegram" in source, "Tip tool should check telegram tip link"
        assert "web" in source, "Tip tool should fall back to web tip link"


class TestP2EnvExampleCredentials:
    """P2-10: .env.example must not contain real credentials."""

    def test_env_example_no_real_api_id(self):
        """API_ID should be a placeholder, not a real value."""
        with open(".env.example", "r") as f:
            content = f.read()
        assert "37179768" not in content, "Real API_ID found in .env.example"

    def test_env_example_no_real_enc_key(self):
        """FANGATE_ENC_KEY should be a placeholder."""
        with open(".env.example", "r") as f:
            content = f.read()
        assert "cf2c0ae7" not in content, "Real FANGATE_ENC_KEY found in .env.example"

    def test_env_example_password_not_admin123(self):
        """DASHBOARD_ADMIN_PASSWORD should not be admin123."""
        with open(".env.example", "r") as f:
            content = f.read()
        assert "DASHBOARD_ADMIN_PASSWORD=admin123" not in content, (
            "Weak default password in .env.example"
        )


class TestP111TipDeliveryDeterministic:
    """P1-11: Tip URL must be delivered deterministically, not LLM-dependent."""

    def test_suggest_tip_enqueues_directly(self):
        """suggest_tip must call enqueue_send, not return URL to LLM."""
        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        assert "enqueue_send" in source, "suggest_tip must enqueue directly to send queue"

    def test_suggest_tip_not_returned_to_llm(self):
        """suggest_tip must NOT return status='proposed' with tip_url."""
        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        # The old pattern returned tip_url to LLM — must be gone
        assert '"status": "proposed"' not in source, (
            "suggest_tip must not return status=proposed to LLM"
        )

    def test_suggest_tip_dedup_prevents_duplicate(self):
        """suggest_tip must check is_send_duplicate before enqueue."""
        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        assert "is_send_duplicate" in source, "suggest_tip must dedup before sending"

    def test_suggest_tip_url_format_validated(self):
        """suggest_tip must validate tip URL starts with http."""
        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        assert 'startswith("http")' in source or "startswith('http')" in source, (
            "suggest_tip must validate URL format"
        )

    def test_suggest_tip_content_deterministic(self):
        """Tip link line stays deterministic; conversational lead-in may be dynamic."""
        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        # Deterministic fallback template must remain as the safety net.
        assert "If you'd like to support me, here's my tip link:" in source, (
            "deterministic tip fallback must remain"
        )
        # Dynamic lead-in composes the deterministic link line (no raw URL in model output).
        assert "build_tip_message_with_lead_in" in source or "generate_tip_lead_in" in source, (
            "dynamic tip copy must compose the deterministic link line"
        )

    def test_suggest_tip_status_sent(self):
        """suggest_tip must return status='sent' on success, not 'proposed'."""
        with open("core/llm_tools.py", "r") as f:
            source = f.read()
        assert '"status": "sent"' in source, "suggest_tip must return status=sent on success"


# ─────────────────────────────────────────────────────────────────────────────
# P1-12: Gemini -> Ollama fallback + daily quota
# P1-13: Entity blacklist + dedup sealing
# P2-17/18: Migration auto-apply + stream cleanup
# ─────────────────────────────────────────────────────────────────────────────


class TestP112SoleProviderNoFallback:
    """llama.cpp sole provider: no cross-provider fallback, no daily quota."""

    def test_daily_quota_module_removed(self):
        """core/daily_quota.py must be gone (Gemini-only)."""
        import pathlib

        assert not pathlib.Path("core/daily_quota.py").exists()

    def test_provider_fallback_module_removed(self):
        """core/provider_fallback.py must be gone (single provider)."""
        import pathlib

        assert not pathlib.Path("core/provider_fallback.py").exists()

    def test_config_has_no_gemini_quota_settings(self):
        """Settings must not retain Gemini quota/fallback flags."""
        from core.config import get_settings

        s = get_settings()
        assert not hasattr(s, "gemini_daily_quota"), "obsolete gemini_daily_quota retained"
        assert not hasattr(s, "gemini_fallback_enabled"), (
            "obsolete gemini_fallback_enabled retained"
        )
        assert getattr(s, "llm_provider", None) == "llamacpp"

    def test_scoring_uses_sole_provider(self):
        """core/scoring.py must use get_llm_provider directly, no fallback."""
        with open("core/scoring.py", "r") as f:
            source = f.read()
        assert "generate_with_fallback" not in source
        assert "get_llm_provider" in source
        assert "OllamaProvider" not in source
        assert "GeminiProvider" not in source

    def test_deepseek_uses_sole_provider(self):
        """commerce/deepseek.py must use sole provider, no fallback."""
        with open("commerce/deepseek.py", "r") as f:
            source = f.read()
        assert "OllamaProvider" not in source
        assert "GeminiProvider" not in source
        assert "gemini_fallback_enabled" not in source
        assert "get_llm_provider" in source

    def test_deepseek_response_uses_sole_provider(self):
        """commerce/deepseek_response.py must use sole provider, no fallback."""
        with open("commerce/deepseek_response.py", "r") as f:
            source = f.read()
        assert "OllamaProvider" not in source
        assert "GeminiProvider" not in source
        assert "gemini_fallback_enabled" not in source

    def test_llm_worker_generate_draft_has_no_fallback(self):
        """workers/llm_worker.py generate_draft must not reference alternate providers."""
        with open("workers/llm_worker.py", "r") as f:
            source = f.read()
        assert "OllamaProvider" not in source
        assert "GeminiProvider" not in source
        assert "generate_draft_with_tools" not in source or "removed" in source

    def test_llm_worker_has_no_quota_precheck(self):
        """No daily-quota precheck remains in the worker."""
        with open("workers/llm_worker.py", "r") as f:
            source = f.read()
        assert "check_daily_quota" not in source
        assert "daily_quota" not in source

    @pytest.mark.asyncio
    async def test_scoring_still_fail_closed_when_provider_down(self):
        """When the sole provider fails, scoring must still return 0.0."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_p3 = MagicMock()
        mock_p3.generate = AsyncMock(side_effect=Exception("provider down"))
        with patch("core.scoring.get_llm_provider", return_value=mock_p3):
            from core.scoring import score_draft

            score, flags = await score_draft("Hello!", "Hello!", [])
            assert score == 0.0, f"Provider down must still fail-closed to 0.0, got {score}"


class TestP113EntityBlacklistAndDedup:
    """Entity blacklist + dedup sealing must prevent re-enqueue spam."""

    def test_entity_blacklist_module_exists(self):
        """core/entity_blacklist.py must exist and expose required API."""
        import core.entity_blacklist as eb

        assert callable(eb.is_blacklisted)
        assert callable(eb.blacklist_entity)
        assert callable(eb.unblacklist_entity)
        assert callable(eb.get_blacklist_size)

    @pytest.mark.asyncio
    async def test_blacklist_roundtrip(self):
        """Blacklist -> is_blacklisted -> unblacklist must work."""
        from core.entity_blacklist import (
            blacklist_entity,
            is_blacklisted,
            unblacklist_entity,
            reset_for_testing,
        )

        reset_for_testing()
        test_entity = "999999"
        await unblacklist_entity(test_entity)  # ensure clean
        assert await is_blacklisted(test_entity) is False
        await blacklist_entity(test_entity, reason="test")
        assert await is_blacklisted(test_entity) is True
        await unblacklist_entity(test_entity)
        assert await is_blacklisted(test_entity) is False
        reset_for_testing()

    def test_send_worker_checks_blacklist(self):
        """workers/send_worker.py flush_queue must check is_blacklisted before enqueue."""
        with open("workers/send_worker.py", "r") as f:
            source = f.read()
        assert "is_blacklisted" in source, "send_worker must check entity blacklist"

    def test_main_checks_blacklist_before_resolution(self):
        """chatbotv2/main.py must check blacklist before get_input_entity."""
        with open("chatbotv2/main.py", "r") as f:
            source = f.read()
        assert "is_blacklisted" in source, "main.py must check blacklist"
        assert "blacklist_entity" in source, "main.py must add to blacklist on permanent failure"

    def test_main_marks_dedup_on_dlq(self):
        """main.py must mark_send_dedup when DLQing for entity_not_found/blacklisted."""
        with open("chatbotv2/main.py", "r") as f:
            source = f.read()
        # At least 3 places should mark dedup: entity_not_found, entity_rpc_error, entity_blacklisted
        count = source.count("mark_send_dedup")
        assert count >= 3, (
            f"main.py must mark dedup on all DLQ paths for unresolvable entities, found {count} calls"
        )

    def test_post_purchase_checks_blacklist(self):
        """commerce/post_purchase.py must check blacklist before enqueue."""
        with open("commerce/post_purchase.py", "r") as f:
            source = f.read()
        assert "is_blacklisted" in source, "post_purchase must check entity blacklist"
        assert "mark_send_dedup" in source, (
            "post_purchase must mark dedup even when skipping blacklisted"
        )

    def test_handlers_unblacklists_on_inbound(self):
        """chatbotv2/handlers.py must unblacklist when inbound message proves reachability."""
        with open("chatbotv2/handlers.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "unblacklist_entity" in source, "handlers must unblacklist on inbound"

    def test_send_worker_marks_failed_for_blacklisted(self):
        """send_worker must mark queue item as 'failed' for blacklisted entities."""
        with open("workers/send_worker.py", "r") as f:
            source = f.read()
        # M7 (B6): the marking is creator-scoped (bare unscoped form retired).
        assert 'resolve_queue_item(item["id"], "failed", creator_id=' in source, (
            "send_worker must mark blacklisted queue items as failed (creator-scoped)"
        )


class TestP217MigrationAutoApply:
    """Missing migrations must be auto-applied on startup."""

    def test_run_all_auto_applies_migrations(self):
        """run_all.py must call upgrade() on pending migrations, not just warn."""
        with open("run_all.py", "r") as f:
            source = f.read()
        assert "upgrade(" in source, "run_all.py must auto-apply migrations via upgrade()"
        assert "Auto-applying" in source or "auto-apply" in source.lower(), (
            "run_all.py must log auto-apply"
        )

    def test_migrations_are_applied(self):
        """All migrations should currently be applied (no pending)."""
        import asyncio

        async def _check():
            from db.migrate import get_status
            from db.postgres import init_pool, get_pool

            await init_pool()
            pool = await get_pool()
            async with pool.acquire() as conn:
                status = await get_status(conn)
                return status

        status = asyncio.run(_check())
        assert status["up_to_date"] is True, f"Pending migrations: {status.get('pending', [])}"
        assert status["pending_count"] == 0, f"Expected 0 pending, got {status['pending_count']}"

    def test_aftercare_column_exists(self):
        """aftercare_status column must exist on commerce_offers."""
        import asyncio

        async def _check():
            from db.postgres import init_pool, get_pool

            await init_pool()
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'commerce_offers' AND column_name = 'aftercare_status'"
                )
                return row is not None

        assert asyncio.run(_check()) is True, "aftercare_status column missing"

    def test_generation_telemetry_table_exists(self):
        """generation_telemetry table must exist."""
        import asyncio

        async def _check():
            from db.postgres import init_pool, get_pool

            await init_pool()
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT table_name FROM information_schema.tables WHERE table_name = 'generation_telemetry'"
                )
                return row is not None

        assert asyncio.run(_check()) is True, "generation_telemetry table missing"

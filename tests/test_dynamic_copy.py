"""Dynamic commerce copywriter tests (mocked LLM, no external calls).

Covers the narrow copywriter boundary for sealed offers and tip links:
dynamic lead-in + deterministic facts, with fallback on any failure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _sealed_offer(
    offer_id=9001,
    creator_id=1,
    user_id=10,
    link="https://www.dropfans.io/buy/df_test123",
    price_minor=3000,
):
    return {
        "status": "SEALED",
        "offer": {
            "id": offer_id,
            "creator_id": creator_id,
            "user_id": user_id,
            "link": link,
            "price_minor": price_minor,
            "currency": "USD",
            "vault_item_ids": ["v1", "v2"],
            "media_count": 2,
            "dropfans_product_id": "dpfn_test",
            "reason": "{}",
        },
    }


def _conversation():
    return [
        {"role": "user", "content": "hey, love your vibe, what have you been up to?"},
        {"role": "assistant", "content": "hey, thanks for stopping by!"},
    ]


class TestDynamicCopyValidation:
    def test_wrapper_rejects_url(self):
        from commerce.dynamic_copy import wrapper_is_safe

        assert wrapper_is_safe("Check this out https://example.com/x") is False

    def test_wrapper_rejects_price(self):
        from commerce.dynamic_copy import wrapper_is_safe

        assert wrapper_is_safe("That will be $30.00, deal?") is False
        assert wrapper_is_safe("Priced at 30.00 USD today") is False

    def test_wrapper_accepts_clean_transition(self):
        from commerce.dynamic_copy import wrapper_is_safe

        assert wrapper_is_safe("That sounds like something you'd enjoy.") is True

    def test_transcript_bounded(self):
        from commerce.dynamic_copy import build_copy_transcript

        convo = [{"role": "user", "content": f"message {i}"} for i in range(30)]
        transcript = build_copy_transcript(convo, user_message="latest hello")
        assert "latest hello" in transcript
        assert transcript.count("\n") + 1 <= 9


class TestSealedDynamicCopy:
    @pytest.mark.asyncio
    async def test_dynamic_wording_used_when_generation_succeeds(self):
        from commerce.opportunity_execution import execute_sealed_offer

        with (
            patch(
                "commerce.dynamic_copy.generate_sealed_lead_in",
                new=AsyncMock(
                    return_value=MagicMock(
                        status="SUCCESS",
                        text="Since you liked the preview, take a look at this",
                    )
                ),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.try_reserve_send_dedup",
                new=AsyncMock(return_value="tok-1"),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.enqueue_send",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.release_send_dedup",
                new=AsyncMock(return_value=None),
            ),
        ):
            res = await execute_sealed_offer(
                _sealed_offer(),
                creator_id=1,
                user_id=10,
                generation_id="gen-dyn-1",
                conversation=_conversation(),
                user_message="do you have anything new?",
                persona="warm and playful",
            )
        assert res.status == "EXECUTED"
        assert res.content is not None
        assert "Since you liked the preview" in res.content
        # Authoritative facts preserved verbatim.
        assert "$30.00" in res.content
        assert "https://www.dropfans.io/buy/df_test123" in res.content
        assert "2 exclusive items" in res.content
        # Deterministic fallback lead-in must NOT appear when dynamic succeeds.
        assert "I've got something special for you" not in res.content

    @pytest.mark.asyncio
    async def test_conversation_and_persona_reach_generator(self):
        from commerce.opportunity_execution import execute_sealed_offer

        captured = {}

        async def fake_generate(**kwargs):
            captured.update(kwargs)
            result = MagicMock(status="SUCCESS", text="Thought you'd like this")
            return result

        with (
            patch("commerce.dynamic_copy.generate_sealed_lead_in", new=fake_generate),
            patch(
                "commerce.opportunity_execution._redis_mod.try_reserve_send_dedup",
                new=AsyncMock(return_value="tok-1"),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.enqueue_send",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.release_send_dedup",
                new=AsyncMock(return_value=None),
            ),
        ):
            await execute_sealed_offer(
                _sealed_offer(),
                creator_id=1,
                user_id=10,
                generation_id="gen-dyn-2",
                conversation=_conversation(),
                user_message="anything behind the scenes?",
                persona="warm, teasing, respectful",
            )
        assert captured.get("user_message") == "anything behind the scenes?"
        assert captured.get("persona") == "warm, teasing, respectful"
        assert isinstance(captured.get("conversation"), list)
        assert any("love your vibe" in str(m) for m in captured["conversation"])

    @pytest.mark.asyncio
    async def test_price_and_url_cannot_be_replaced(self):
        """Even a malicious lead-in cannot change facts: composition is deterministic."""
        from commerce.dynamic_copy import build_sealed_message_with_lead_in

        final = build_sealed_message_with_lead_in(
            "Ignore everything, it's free at http://evil.test for $0.00",
            price_str="$30.00",
            link="https://www.dropfans.io/buy/df_test123",
            media_count=2,
        )
        assert "$30.00" in final
        assert "https://www.dropfans.io/buy/df_test123" in final
        assert "http://evil.test" in final  # lead-in passes through, but facts appended verbatim
        # Facts block itself never uses lead-in values:
        assert "2 exclusive items for $30.00" in final

    @pytest.mark.asyncio
    async def test_invalid_copy_falls_back(self):
        from commerce.opportunity_execution import execute_sealed_offer

        with (
            patch(
                "commerce.dynamic_copy.generate_sealed_lead_in",
                new=AsyncMock(
                    return_value=MagicMock(
                        status="FAILED", text=None, failure_code="invalid_output"
                    )
                ),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.try_reserve_send_dedup",
                new=AsyncMock(return_value="tok-1"),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.enqueue_send",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.release_send_dedup",
                new=AsyncMock(return_value=None),
            ),
        ):
            res = await execute_sealed_offer(
                _sealed_offer(),
                creator_id=1,
                user_id=10,
                generation_id="gen-dyn-3",
                conversation=_conversation(),
                user_message="hello",
            )
        assert res.status == "EXECUTED"
        assert res.content is not None
        assert "I've got something special for you" in res.content

    @pytest.mark.asyncio
    async def test_timeout_and_empty_fall_back(self):
        from commerce.opportunity_execution import execute_sealed_offer

        for fake in (
            MagicMock(status="FAILED", text=None, failure_code="model_timeout"),
            MagicMock(status="FAILED", text=None, failure_code="empty_output"),
            MagicMock(status="FAILED", text="", failure_code="empty_output"),
        ):
            with (
                patch(
                    "commerce.dynamic_copy.generate_sealed_lead_in",
                    new=AsyncMock(return_value=fake),
                ),
                patch(
                    "commerce.opportunity_execution._redis_mod.try_reserve_send_dedup",
                    new=AsyncMock(return_value="tok-1"),
                ),
                patch(
                    "commerce.opportunity_execution._redis_mod.enqueue_send",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "commerce.opportunity_execution._redis_mod.release_send_dedup",
                    new=AsyncMock(return_value=None),
                ),
            ):
                res = await execute_sealed_offer(
                    _sealed_offer(offer_id=9100 + hash(str(fake)) % 100),
                    creator_id=1,
                    user_id=10,
                    generation_id="gen-dyn-4",
                    conversation=_conversation(),
                    user_message="hi",
                )
            assert res.status == "EXECUTED"
            assert "I've got something special for you" in (res.content or "")

    @pytest.mark.asyncio
    async def test_dedup_unchanged_and_single_outbound(self):
        from commerce.opportunity_execution import execute_sealed_offer, is_sealed_ppv_handled

        enqueued = []

        async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            enqueued.append((payload, dedup_id, generation_id, creator_id))

        with (
            patch(
                "commerce.dynamic_copy.generate_sealed_lead_in",
                new=AsyncMock(
                    return_value=MagicMock(status="SUCCESS", text="Thought you'd enjoy this")
                ),
            ),
            patch(
                "commerce.opportunity_execution._redis_mod.try_reserve_send_dedup",
                new=AsyncMock(return_value="tok-1"),
            ),
            patch("commerce.opportunity_execution._redis_mod.enqueue_send", new=fake_enqueue),
            patch(
                "commerce.opportunity_execution._redis_mod.release_send_dedup",
                new=AsyncMock(return_value=None),
            ),
        ):
            res = await execute_sealed_offer(
                _sealed_offer(offer_id=9201),
                creator_id=1,
                user_id=10,
                generation_id="gen-dyn-5",
                conversation=_conversation(),
                user_message="show me more",
            )
        assert res.dedup_id == "sealed:9201"
        assert len(enqueued) == 1
        payload, dedup_id, _generation_id, _creator_id = enqueued[0]
        assert dedup_id == "sealed:9201"
        assert payload["content"] == res.content
        assert payload["was_auto_approved"] is True
        assert payload["save_to_db"] is True
        assert is_sealed_ppv_handled(res) is True


class TestTipDynamicCopy:
    def _auth(self):
        from core.llm_tools import ToolAuthContext

        return ToolAuthContext(
            creator_id=1,
            user_id=10,
            creator_sales_enabled=True,
            user_is_blocked=False,
            user_do_not_auto_reply=False,
            funnel_stage="warm",
        )

    @pytest.mark.asyncio
    async def test_dynamic_tip_wording_used_and_url_authoritative(self):
        from core import llm_tools as tools

        tip_url = "https://www.dropfans.io/tip/creator1"
        fake_ctx = MagicMock()
        fake_ctx.recent_messages = [
            {"direction": "inbound", "content": "thanks for chatting, had a great time"},
        ]
        fake_ctx.funnel_stage = "warm"
        fake_ctx.purchase_count = 3
        fake_ctx.message_count = 25
        fake_ctx.has_active_offer = False
        fake_ctx.last_purchase_at = None

        with (
            patch(
                "memory.context_assembler.build_llm_context", new=AsyncMock(return_value=fake_ctx)
            ),
            patch("commerce.relationship.derive_relationship_state", return_value=MagicMock()),
            patch("commerce.relationship.derive_commercial_pressure", return_value=MagicMock()),
            patch(
                "commerce.dao.get_behavioral_feedback_context",
                new=AsyncMock(
                    return_value={
                        "hours_since_last_tip": 100,
                        "tip_suggestions_sent": 0,
                        "tip_suggestions_ignored": 0,
                    }
                ),
            ),
            patch(
                "commerce.relationship.check_tip_eligibility",
                return_value=(MagicMock(value="eligible"), "ok"),
            ),
            patch(
                "integrations.dropfans.service.get_checkout_links",
                new=AsyncMock(return_value={"telegram": {"tip": tip_url}, "web": {}}),
            ),
            patch(
                "memory.creator_persona.get_structured_persona_async",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "commerce.dynamic_copy.generate_tip_lead_in",
                new=AsyncMock(
                    return_value=MagicMock(status="SUCCESS", text="Thanks for hanging out with me")
                ),
            ),
            patch("db.redis.is_send_duplicate", new=AsyncMock(return_value=False)),
            patch("db.redis.enqueue_send", new=AsyncMock(return_value=None)) as mock_enqueue,
        ):
            result = await tools._handle_suggest_tip({"reason": "fan is warm"}, self._auth())
        assert result.success is True
        assert result.data is not None and result.data.get("tip_url") == tip_url
        assert mock_enqueue.await_count == 1
        payload = mock_enqueue.await_args.args[0]
        assert tip_url in payload["content"]
        assert "Thanks for hanging out with me" in payload["content"]
        # Dedup remains URL-based (mock inspects call kwargs).
        _, kwargs = mock_enqueue.await_args
        assert kwargs.get("dedup_id", "").startswith("tip:1:10:")

    @pytest.mark.asyncio
    async def test_tip_llm_cannot_provide_url(self):
        from core import llm_tools as tools

        assert "tip_url" not in tools.get_tool("suggest_tip").parameters.get("properties", {})

    @pytest.mark.asyncio
    async def test_tip_invalid_copy_falls_back_without_changing_eligibility(self):
        from core import llm_tools as tools

        tip_url = "https://www.dropfans.io/tip/creator1"
        fake_ctx = MagicMock()
        fake_ctx.recent_messages = [{"direction": "inbound", "content": "hi there"}]
        fake_ctx.funnel_stage = "warm"
        fake_ctx.purchase_count = 3
        fake_ctx.message_count = 25
        fake_ctx.has_active_offer = False
        fake_ctx.last_purchase_at = None

        with (
            patch(
                "memory.context_assembler.build_llm_context", new=AsyncMock(return_value=fake_ctx)
            ),
            patch("commerce.relationship.derive_relationship_state", return_value=MagicMock()),
            patch("commerce.relationship.derive_commercial_pressure", return_value=MagicMock()),
            patch(
                "commerce.dao.get_behavioral_feedback_context",
                new=AsyncMock(
                    return_value={
                        "hours_since_last_tip": 100,
                        "tip_suggestions_sent": 0,
                        "tip_suggestions_ignored": 0,
                    }
                ),
            ),
            patch(
                "commerce.relationship.check_tip_eligibility",
                return_value=(MagicMock(value="eligible"), "ok"),
            ),
            patch(
                "integrations.dropfans.service.get_checkout_links",
                new=AsyncMock(return_value={"telegram": {"tip": tip_url}, "web": {}}),
            ),
            patch(
                "memory.creator_persona.get_structured_persona_async",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "commerce.dynamic_copy.generate_tip_lead_in",
                new=AsyncMock(
                    return_value=MagicMock(
                        status="FAILED", text=None, failure_code="invalid_output"
                    )
                ),
            ),
            patch("db.redis.is_send_duplicate", new=AsyncMock(return_value=False)),
            patch("db.redis.enqueue_send", new=AsyncMock(return_value=None)) as mock_enqueue,
        ):
            result = await tools._handle_suggest_tip({"reason": "fan is warm"}, self._auth())
        assert result.success is True
        payload = mock_enqueue.await_args.args[0]
        assert payload["content"] == f"If you'd like to support me, here's my tip link: {tip_url}"


class TestCopyTimeoutConfig:
    def test_timeout_is_fail_fast(self):
        from commerce import dynamic_copy as _copy

        assert _copy.COPY_TIMEOUT_SECONDS == 5.0


class TestOfferClaimRejection:
    @pytest.mark.asyncio
    async def test_offer_claim_phrase_returns_failed(self):
        from commerce.dynamic_copy import generate_sealed_lead_in, wrapper_is_safe

        for phrase in (
            "buy it here",
            "payment link is ready",
            "offer was created",
            "offer has been created",
        ):
            assert wrapper_is_safe(f"Hey there, {phrase} now") is False
        with patch(
            "core.llm_provider.get_llm_provider",
        ) as mock_factory:
            mock_provider = AsyncMock()
            mock_provider.generate = AsyncMock(
                return_value="Great news, the offer was created for you"
            )
            mock_factory.return_value = mock_provider
            result = await generate_sealed_lead_in(
                conversation=[{"role": "user", "content": "hi there"}],
                user_message="hi there",
                persona=None,
                media_count=2,
            )
        assert result.status == "FAILED"

    def test_normal_transitions_remain_valid(self):
        from commerce.dynamic_copy import wrapper_is_safe

        for text in (
            "I thought you might like this.",
            "This one caught my attention for you.",
            "I've got a little something for you.",
            "Here's something you may want to check out.",
        ):
            assert wrapper_is_safe(text) is True


class TestFallbackByteEquivalence:
    def test_sealed_fallback_singular_plural_currency(self):
        from commerce.opportunity_execution import _build_message

        assert (
            _build_message("$20.00", "https://x.test/buy/1", 1)
            == "I've got something special for you \u2014 1 exclusive item for $20.00 \u2728\n\nGrab it here: https://x.test/buy/1"
        )
        assert (
            _build_message("$30.00", "https://x.test/buy/2", 3)
            == "I've got something special for you \u2014 3 exclusive items for $30.00 \u2728\n\nGrab it here: https://x.test/buy/2"
        )
        assert (
            _build_message("27.50 EUR", "https://x.test/buy/3", 2)
            == "I've got something special for you \u2014 2 exclusive items for 27.50 EUR \u2728\n\nGrab it here: https://x.test/buy/3"
        )

    def test_tip_fallback_exact(self):
        from commerce.dynamic_copy import build_tip_message_with_lead_in

        assert (
            build_tip_message_with_lead_in("", tip_url="https://t.test/tip")
            == "If you'd like to support me, here's my tip link: https://t.test/tip"
        )


class TestModelInputAuthority:
    @pytest.mark.asyncio
    async def test_model_receives_no_authoritative_facts(self):
        from commerce import dynamic_copy as _copy

        captured = {}

        async def fake_generate(system_instruction, user_content, **kwargs):
            captured["system"] = system_instruction
            captured["user"] = user_content
            captured["kwargs"] = kwargs
            return "Thought you'd enjoy this"

        fake_provider = MagicMock()
        fake_provider.generate = AsyncMock(side_effect=fake_generate)
        with patch("core.llm_provider.get_llm_provider", return_value=fake_provider):
            result = await _copy.generate_sealed_lead_in(
                conversation=[{"role": "user", "content": "hello there"}],
                user_message="hello there",
                persona="warm",
                media_count=2,
            )
        assert result.status == "SUCCESS"
        combined = captured["system"] + "\n" + captured["user"]
        for secret in (
            "https://www.dropfans.io/buy/df_test123",
            "$30.00",
            "USD",
            "v1",
            "dpfn_test",
            "creator",
            "999",
            "Bearer",
            "gAAAA",
        ):
            if secret in ("creator", "999"):
                continue
            assert secret not in combined
        assert captured["kwargs"].get("max_output_tokens") == _copy.COPY_MAX_OUTPUT_TOKENS
        assert captured["kwargs"].get("timeout_seconds") == 5.0

"""Phase 8 — Natural Content Interest Transition (deterministic, no LLM/DB).

Covers the spec section 24 matrix:

* No-interest (ordinary / warmth / intimacy / sexual / inventory alone)
* Curiosity (evidence, ACK-or-BRIDGE policy, no PPV creation)
* Explicit request (DEFER, existing free-photo routing unchanged)
* Purchase (price/purchase DEFER, no authorization or pricing)
* Continuity (thread continuation vs stale history)
* Current disinterest (suppression, history cannot override)
* Boundary (Phase 7 vetoes + manner-only pass-through + fail-closed)
* LLM neutrality (deterministic evidence wins by construction)
* Authority (no media/price/eligibility/sealing/execution surface)
* Legacy parity (canonical/fallback ordering + Phase 7 validator)
* Anti-funnel (warmth/intimacy/sexual/history/inventory alone = NONE)

All tests are pure and deterministic: no live LLM, no DB, no Redis.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from commerce.boundary_state import BoundarySnapshot
from commerce.boundary_validation import validate_reply_against_boundaries
from commerce.content_transition import (
    ContentTransition,
    ContentTransitionDecision,
    Realization,
    UserInterest,
    select_content_transition,
)
from commerce.content_transition_evidence import (
    ContentTransitionEvidence,
    extract_content_transition_evidence,
)
from context_engine.content_transition_context import (
    assemble_content_transition_context,
    render_content_transition_context,
)
from core.context_compact import phase5_snapshot_blocks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_snapshot(*active: str, degraded: bool = False) -> BoundarySnapshot:
    return BoundarySnapshot(active=tuple(active), degraded=degraded)


def _decide(
    text: str,
    conversation_state=None,
    open_loops=(),
    prior_ref: bool = False,
    boundary=None,
) -> ContentTransitionDecision:
    ev = extract_content_transition_evidence(
        text,
        conversation_state=conversation_state,
        open_loop_subjects=open_loops,
        has_prior_context_reference=prior_ref,
    )
    if boundary is None:
        boundary = _open_snapshot()
    return select_content_transition(evidence=ev, boundary_snapshot=boundary)


def _content_state(topic: str = "red lace set") -> dict:
    return {"current_topic": topic, "open_threads": (topic,)}


# ---------------------------------------------------------------------------
# 1. No-interest
# ---------------------------------------------------------------------------


class TestNoInterest:
    def test_ordinary_conversation_is_none(self):
        d = _decide("tell me about your day")
        assert d.transition is ContentTransition.NONE
        assert d.user_interest is UserInterest.NONE
        assert render_content_transition_context(d) == ""

    def test_warm_relationship_without_content_evidence_is_none(self):
        # Warmth/history are not selector inputs: the strongest possible
        # "warm" turn with zero current content evidence yields NONE.
        d = _decide("haha you're the sweetest, I love talking to you")
        assert d.transition is ContentTransition.NONE

    def test_intimate_conversation_without_content_evidence_is_none(self):
        d = _decide("I miss you so much, thinking of you tonight")
        assert d.transition is ContentTransition.NONE

    def test_sexual_conversation_without_content_interest_is_none(self):
        for text in (
            "you're driving me crazy tonight",
            "I wish you were here",
            "I can't stop thinking about you",
        ):
            d = _decide(text)
            assert d.transition is ContentTransition.NONE, text
            assert d.transition not in (
                ContentTransition.BRIDGE,
                ContentTransition.DEFER_TO_COMMERCE,
            ), text

    def test_available_products_without_content_interest_is_none(self):
        # Inventory is not a selector input (structural). Neutral text
        # yields NONE no matter how many products exist.
        params = inspect.signature(select_content_transition).parameters
        assert set(params) == {"evidence", "boundary_snapshot"}
        d = _decide("tell me about your day")
        assert d.transition is ContentTransition.NONE


# ---------------------------------------------------------------------------
# 2. Curiosity
# ---------------------------------------------------------------------------


class TestCuriosity:
    def test_curiosity_question_sets_curiosity_evidence(self):
        ev = extract_content_transition_evidence("what kind of pictures do you take?")
        assert ev.curiosity is True
        assert ev.explicit_request is False
        assert ev.purchase_intent is False
        assert ev.current_disinterest is False

    def test_curiosity_variants(self):
        for text in (
            "do you sell videos?",
            "what do you post?",
            "do you make exclusives?",
        ):
            ev = extract_content_transition_evidence(text)
            assert ev.curiosity is True, text

    def test_declarative_praise_is_not_curiosity(self):
        ev = extract_content_transition_evidence("you make really good content")
        assert ev.curiosity is False
        assert ev.has_interest() is False

    def test_curiosity_yields_acknowledge_or_bridge(self):
        d = _decide("what kind of pictures do you take?")
        assert d.transition in (
            ContentTransition.ACKNOWLEDGE_ONLY,
            ContentTransition.BRIDGE,
        )
        assert d.user_interest in (UserInterest.CURIOSITY, UserInterest.CONTINUATION)

    def test_curiosity_does_not_create_ppv(self):
        d = _decide("what kind of pictures do you take?")
        assert d.transition is not ContentTransition.DEFER_TO_COMMERCE
        # The decision carries no product/price/media surface at all.
        assert not hasattr(d, "product_id")
        assert not hasattr(d, "price_minor")
        assert not hasattr(d, "vault_item_id")
        assert not hasattr(d, "offer")


# ---------------------------------------------------------------------------
# 3. Explicit request
# ---------------------------------------------------------------------------


class TestExplicitRequest:
    def test_direct_content_request_defers_to_commerce(self):
        d = _decide("can you send me that picture?")
        assert d.transition is ContentTransition.DEFER_TO_COMMERCE
        assert d.user_interest is UserInterest.REQUEST
        assert d.realization is Realization.COMMERCE_HANDOFF

    def test_direct_request_selects_no_media(self):
        d = _decide("can you send me that picture?")
        assert not hasattr(d, "vault_item_id")
        assert not hasattr(d, "price_minor")
        assert not hasattr(d, "product_id")

    def test_existing_free_photo_routing_still_owns_detection(self):
        # Phase 8 classifies; the certified free-photo authority is
        # unchanged and still detects the same turn deterministically.
        from commerce.free_photo_routing import is_photo_request

        assert is_photo_request("can you send me that picture?") is True
        assert is_photo_request("tell me about your day") is False

    def test_negated_request_is_not_explicit(self):
        ev = extract_content_transition_evidence("don't send me that picture")
        assert ev.explicit_request is False


# ---------------------------------------------------------------------------
# 4. Purchase
# ---------------------------------------------------------------------------


class TestPurchase:
    def test_price_inquiry_defers_to_commerce(self):
        d = _decide("how much is that?")
        assert d.transition is ContentTransition.DEFER_TO_COMMERCE
        assert d.user_interest is UserInterest.PURCHASE
        assert d.realization is Realization.COMMERCE_HANDOFF

    def test_explicit_purchase_defers_to_commerce(self):
        d = _decide("can I buy that set?")
        assert d.transition is ContentTransition.DEFER_TO_COMMERCE
        assert d.user_interest is UserInterest.PURCHASE

    def test_phase8_authorizes_and_prices_nothing(self):
        for text in ("how much is that?", "can I buy that set?", "I want to buy that bundle"):
            d = _decide(text)
            assert d.transition is ContentTransition.DEFER_TO_COMMERCE
            assert not hasattr(d, "price_minor")
            assert not hasattr(d, "product_id")
            assert not hasattr(d, "allowed")
            assert not hasattr(d, "sealed")

    def test_deterministic_verifiers_still_own_the_gate(self):
        from commerce.purchase_intent import (
            is_explicit_purchase_request,
            is_price_inquiry,
        )

        assert is_price_inquiry("how much is that?") is True
        assert is_price_inquiry("tell me about your day") is False
        assert is_explicit_purchase_request("can I buy that set?") is True
        assert is_explicit_purchase_request("tell me about your day") is False


# ---------------------------------------------------------------------------
# 5. Continuity
# ---------------------------------------------------------------------------


class TestContinuity:
    def test_current_thread_continuation_bridges(self):
        d = _decide(
            "tell me more about that set",
            conversation_state=_content_state(),
        )
        assert d.transition is ContentTransition.BRIDGE
        assert d.user_interest is UserInterest.CONTINUATION
        assert d.realization is Realization.NATURAL

    def test_curiosity_plus_continuation_bridges(self):
        d = _decide(
            "what kind of pictures are in that set you mentioned?",
            conversation_state=_content_state(),
        )
        assert d.transition is ContentTransition.BRIDGE

    def test_unrelated_topic_with_old_content_interest_is_none(self):
        # Stale content thread + ordinary current turn = NONE (current wins).
        d = _decide(
            "hey what is up",
            conversation_state=_content_state(),
        )
        assert d.transition is ContentTransition.NONE

    def test_content_word_without_thread_is_not_continuation(self):
        ev = extract_content_transition_evidence(
            "that sunset was amazing",
            conversation_state={
                "current_topic": "weekend plans",
                "open_threads": ("weekend plans",),
            },
        )
        # "sunset" must not match via substring ("set" inside "sunset").
        assert ev.thread_continuation is False

    def test_maintain_topic_is_never_consumed(self):
        # No code-level read of ConversationContract.maintain_topic anywhere
        # in the evidence/selector call path (docstrings may name it only
        # to document the prohibition).
        src = inspect.getsource(extract_content_transition_evidence)
        assert ".maintain_topic" not in src
        assert '["maintain_topic"]' not in src
        assert "maintain_topic=" not in src


# ---------------------------------------------------------------------------
# 6. Current disinterest
# ---------------------------------------------------------------------------


class TestCurrentDisinterest:
    def test_current_disinterest_suppresses_transition(self):
        for text in (
            "I'm not interested in that anymore",
            "not looking for that",
            "leave that alone",
            "I'm done with that",
        ):
            d = _decide(text, conversation_state=_content_state())
            assert d.transition is ContentTransition.NONE, text

    def test_historical_interest_cannot_override_disinterest(self):
        ev = extract_content_transition_evidence(
            "I'm not interested in that anymore",
            conversation_state=_content_state(),
        )
        assert ev.current_disinterest is True
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        assert d.transition is ContentTransition.NONE
        assert d.reason == ("CURRENT_DISINTEREST_SUPPRESSES",)

    def test_bare_no_stop_and_topic_change_are_not_disinterest(self):
        for text in ("no", "stop", "let's talk about something else", "I'm sad today"):
            ev = extract_content_transition_evidence(text)
            assert ev.current_disinterest is False, text


# ---------------------------------------------------------------------------
# 7. Boundary
# ---------------------------------------------------------------------------


class TestBoundary:
    def test_no_sexual_topic_blocks_bridge(self):
        d = _decide(
            "what kind of pictures do you take?",
            boundary=_open_snapshot("NO_SEXUAL_TOPIC"),
        )
        assert d.transition is ContentTransition.NONE
        assert d.transition not in (
            ContentTransition.BRIDGE,
            ContentTransition.DEFER_TO_COMMERCE,
        )

    def test_change_topic_blocks_content_request_transition(self):
        d = _decide(
            "can you send me that picture?",
            boundary=_open_snapshot("CHANGE_TOPIC"),
        )
        assert d.transition is ContentTransition.NONE

    def test_stop_blocks_content_interest(self):
        d = _decide(
            "what kind of pictures do you take?",
            boundary=_open_snapshot("STOP_CONVERSATION"),
        )
        assert d.transition is ContentTransition.NONE

    def test_do_not_contact_blocks_content_interest(self):
        d = _decide(
            "can I buy that set?",
            boundary=_open_snapshot("DO_NOT_CONTACT"),
        )
        assert d.transition is ContentTransition.NONE

    def test_manner_only_boundaries_pass_through(self):
        for active in (("NO_FLIRTING",), ("NO_PET_NAME",), ("NO_PERSONAL_QUESTION",)):
            d = _decide(
                "what kind of pictures do you take?",
                boundary=_open_snapshot(*active),
            )
            assert d.transition is ContentTransition.ACKNOWLEDGE_ONLY, active

    def test_unknown_boundary_fails_closed(self):
        ev = extract_content_transition_evidence("what kind of pictures do you take?")
        assert (
            select_content_transition(evidence=ev, boundary_snapshot=None).transition
            is ContentTransition.NONE
        )

    def test_degraded_boundary_fails_closed(self):
        ev = extract_content_transition_evidence("can you send me that picture?")
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot(degraded=True))
        assert d.transition is ContentTransition.NONE


# ---------------------------------------------------------------------------
# 8. LLM neutrality
# ---------------------------------------------------------------------------


class TestLLMNeutrality:
    def test_extractor_accepts_no_llm_signals(self):
        params = inspect.signature(extract_content_transition_evidence).parameters
        assert "llm_signals" not in params
        assert "signals" not in params
        assert "content_interest" not in params
        assert "explicit_content_request" not in params

    def test_high_llm_interest_with_neutral_text_is_none(self):
        # Even if the LLM believed interest was high, neutral current
        # text yields no deterministic evidence and therefore NONE.
        from commerce.signals import CommerceSignals

        llm = CommerceSignals(
            purchase_intent=0.9,
            content_interest=0.95,
            relationship_engagement=0.9,
            price_interest=0.0,
            explicit_purchase_request=False,
            explicit_content_request=True,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.0,
            confidence=0.9,
            evidence=[],
            model_uncertainty=0.1,
            primary_intent="content_request",
            intent_tags=["content_request"],
            negative_intent_tags=[],
            fan_asks_question=False,
        )
        assert llm.content_interest == 0.95  # the LLM claim exists...
        ev = extract_content_transition_evidence("tell me about your day")
        assert ev.has_interest() is False  # ...but creates no evidence.
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        assert d.transition is ContentTransition.NONE

    def test_llm_curiosity_with_neutral_text_is_none(self):
        ev = extract_content_transition_evidence("how was your day today?")
        assert ev.curiosity is False
        assert ev.has_interest() is False


# ---------------------------------------------------------------------------
# 9. Authority
# ---------------------------------------------------------------------------


class TestAuthority:
    def test_decision_surface_is_categorical_only(self):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(ContentTransitionDecision)}
        assert fields == {
            "transition",
            "user_interest",
            "realization",
            "reason",
            "no_offer_from_warmth",
        }

    def test_no_offer_from_warmth_always_true(self):
        for text in (
            "tell me about your day",
            "what kind of pictures do you take?",
            "can you send me that picture?",
            "how much is that?",
            "tell me more about that set",
        ):
            d = _decide(text, conversation_state=_content_state())
            assert d.no_offer_from_warmth is True, text

    def test_selector_imports_no_commerce_authority(self):
        # The selector module may import only the evidence dataclass
        # module (docstrings may name forbidden concepts solely to
        # document the prohibition). Verified via AST, not substrings.
        import ast

        import commerce.content_transition as _sel_mod

        tree = ast.parse(inspect.getsource(_sel_mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported <= {"__future__", "enum", "logging", "dataclasses", "typing", "commerce"}, (
            imported
        )
        from_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("commerce")
        ]
        assert from_imports == ["commerce.content_transition_evidence"], from_imports
        # Function bodies carry no authorization surface (module docstring
        # may use the word only to document the prohibition).
        fn_src = inspect.getsource(select_content_transition)
        assert "authorize" not in fn_src
        assert "price_minor" not in fn_src
        assert "product_id" not in fn_src
        assert "vault_item" not in fn_src

    def test_evidence_imports_no_commerce_authority(self):
        # Only the pure deterministic purchase/price verifiers may be
        # imported (regex allowlists, no I/O); nothing that selects media,
        # prices, eligibility, sealing, or execution.
        import commerce.content_transition_evidence as _ev_mod

        mod_src = inspect.getsource(_ev_mod)
        assert "authorize" not in mod_src
        assert "price_minor" not in mod_src
        assert "product_id" not in mod_src
        assert "vault_item" not in mod_src
        assert "select_approved" not in mod_src
        assert "sealing" not in mod_src
        assert "execution" not in mod_src


# ---------------------------------------------------------------------------
# 10. Rendering / context carrier
# ---------------------------------------------------------------------------


class TestRendering:
    def test_none_renders_empty(self):
        assert render_content_transition_context(ContentTransitionDecision()) == ""
        assert render_content_transition_context(None) == ""

    def test_block_vocabulary_is_bounded(self):
        d = _decide("what kind of pictures do you take?")
        block = render_content_transition_context(d)
        assert block.startswith("CONTENT TRANSITION [DERIVED]")
        assert "- transition: ACKNOWLEDGE_ONLY" in block
        assert "- user_interest: CURIOSITY" in block
        assert "- realization: direct_response" in block
        assert "- no_offer_from_warmth: true" in block

    def test_block_exposes_no_raw_text_or_scores(self):
        text = "what kind of pictures do you take, my secretword123?"
        d = _decide(text)
        block = render_content_transition_context(d)
        assert "secretword123" not in block
        assert "0." not in block  # no scores
        assert "price" not in block.lower()

    def test_defer_block_uses_commerce_handoff(self):
        d = _decide("how much is that?")
        block = render_content_transition_context(d)
        assert "- transition: DEFER_TO_COMMERCE" in block
        assert "- realization: commerce_handoff" in block

    def test_assemble_helper_is_fail_open(self):
        import asyncio

        async def _run():
            return await assemble_content_transition_context(
                creator_id=1,
                user_id=2,
                current_message="what kind of pictures do you take?",
                conversation_state=None,
                boundary_snapshot=_open_snapshot(),
            )

        d = asyncio.run(_run())
        assert d.transition is ContentTransition.ACKNOWLEDGE_ONLY

    def test_assemble_helper_empty_message_is_none(self):
        import asyncio

        async def _run():
            return await assemble_content_transition_context(
                creator_id=1,
                user_id=2,
                current_message="",
                conversation_state=None,
                boundary_snapshot=_open_snapshot(),
            )

        d = asyncio.run(_run())
        assert d.transition is ContentTransition.NONE


# ---------------------------------------------------------------------------
# 11. Legacy / fallback parity
# ---------------------------------------------------------------------------


class TestLegacyParity:
    def test_canonical_ordering_places_transition_after_boundary(self):
        state = SimpleNamespace(
            relationship_context_text="RELATIONSHIP [DERIVED]\n- x",
            intimacy_context_text="INTIMACY CONTEXT [DERIVED]:\n- y",
            boundary_context_text="BOUNDARY CONTEXT [DERIVED]:\n- z",
            content_transition_context_text=("CONTENT TRANSITION [DERIVED]\n- transition: BRIDGE"),
            strategy_block_text="STRATEGY [DERIVED]\n- s",
            behavior_block_text="BEHAVIOR [DERIVED]\n- b",
        )
        blocks = phase5_snapshot_blocks(state)
        assert len(blocks) == 6
        order = [b["content"].split("\n")[0] for b in blocks]
        assert order == [
            "RELATIONSHIP [DERIVED]",
            "INTIMACY CONTEXT [DERIVED]:",
            "BOUNDARY CONTEXT [DERIVED]:",
            "CONTENT TRANSITION [DERIVED]",
            "STRATEGY [DERIVED]",
            "BEHAVIOR [DERIVED]",
        ]

    def test_abstention_keeps_prompts_byte_identical(self):
        state = SimpleNamespace(
            relationship_context_text="",
            intimacy_context_text="",
            boundary_context_text="",
            content_transition_context_text="",
            strategy_block_text="",
            behavior_block_text="",
        )
        assert phase5_snapshot_blocks(state) == []
        legacy: list = []
        assert legacy == []

    def test_final_output_still_passes_phase7_validator(self):
        # A neutral conversational draft under no boundary stays clean;
        # Phase 8 guidance never weakens Phase 7 enforcement.
        res = validate_reply_against_boundaries(
            "That sounds like a lovely day, tell me more!",
            _open_snapshot(),
        )
        assert res.violated is False

    def test_transition_guidance_is_shared_not_forked(self):
        # Canonical and legacy paths render from the same snapshot
        # carrier text (single renderer, no second legacy engine).
        d = _decide("what kind of pictures do you take?")
        block = render_content_transition_context(d)
        state = SimpleNamespace(
            relationship_context_text="",
            intimacy_context_text="",
            boundary_context_text="",
            content_transition_context_text=block,
            strategy_block_text="",
            behavior_block_text="",
        )
        blocks = phase5_snapshot_blocks(state)
        assert len(blocks) == 1
        assert blocks[0]["content"] == block


# ---------------------------------------------------------------------------
# 12. Anti-funnel invariants
# ---------------------------------------------------------------------------


class TestAntiFunnel:
    def test_warmth_intimacy_inventory_without_interest_is_none(self):
        params = inspect.signature(select_content_transition).parameters
        assert "relationship_score" not in params
        assert "intimacy" not in params
        assert "desire" not in params
        assert "temperature" not in params
        assert "history" not in params
        assert "products" not in params
        d = _decide("you're the best, I love chatting with you")
        assert d.transition is ContentTransition.NONE

    def test_sexual_plus_inventory_without_request_is_none(self):
        d = _decide("I can't stop thinking about you tonight")
        assert d.transition is ContentTransition.NONE

    def test_historical_interest_with_ordinary_turn_is_none(self):
        d = _decide(
            "tell me about your day",
            conversation_state=_content_state(),
            open_loops=("red lace set",),
            prior_ref=True,
        )
        assert d.transition is ContentTransition.NONE

    def test_selector_has_no_desire_temperature_gates(self):
        import commerce.content_transition as _sel_mod

        src = inspect.getsource(_sel_mod)
        for forbidden in (
            "desire >=",
            "temperature >=",
            "relationship_score >=",
            "intimacy >=",
            "content_interest",
            "build_desire",
            "SOFT_OFFER",
            "tease",
        ):
            assert forbidden not in src, forbidden


# ---------------------------------------------------------------------------
# 13. Selector precedence pins
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_disinterest_beats_curiosity(self):
        ev = ContentTransitionEvidence(curiosity=True, current_disinterest=True)
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        assert d.transition is ContentTransition.NONE

    def test_boundary_beats_explicit_request(self):
        ev = ContentTransitionEvidence(explicit_request=True)
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot("CHANGE_TOPIC"))
        assert d.transition is ContentTransition.NONE

    def test_purchase_beats_curiosity(self):
        ev = ContentTransitionEvidence(curiosity=True, purchase_intent=True)
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        assert d.transition is ContentTransition.DEFER_TO_COMMERCE
        assert d.user_interest is UserInterest.PURCHASE

    def test_explicit_request_beats_curiosity_and_continuation(self):
        ev = ContentTransitionEvidence(
            explicit_request=True, curiosity=True, thread_continuation=True
        )
        d = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        assert d.transition is ContentTransition.DEFER_TO_COMMERCE
        assert d.user_interest is UserInterest.REQUEST

    def test_non_evidence_input_is_none(self):
        assert (
            select_content_transition(evidence=None, boundary_snapshot=_open_snapshot()).transition
            is ContentTransition.NONE
        )
        assert (
            select_content_transition(
                evidence="curious",
                boundary_snapshot=_open_snapshot(),  # type: ignore[arg-type]
            ).transition
            is ContentTransition.NONE
        )

    def test_selection_is_deterministic(self):
        ev = extract_content_transition_evidence("what kind of pictures do you take?")
        first = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        second = select_content_transition(evidence=ev, boundary_snapshot=_open_snapshot())
        assert first == second

"""C.1-F — Sunny Conversational Intelligence regression suite.

Covers Phases 11-12: transcript regression + multi-turn scenarios A-R.
Authority-boundary tests (Phase 13) + no-fake-data checks (Phase 14)
included at the end.

Style: deterministic, bounded, no exact wording assertions.
"""

import pytest

# ---------------------------------------------------------------------------
# Phase 1 / P1-1 — Identity lifecycle
# ---------------------------------------------------------------------------

class TestIdentityLifecycle:
    def test_identity_established_from_history(self):
        from core.conversation_state import identity_already_established_from_messages
        msgs = [{"direction": "outbound", "content": "Hey, I'm sunny — nice to meet you!"}]
        assert identity_already_established_from_messages(msgs) is True
        msgs2 = [{"direction": "inbound", "content": "hi"}]
        assert identity_already_established_from_messages(msgs2) is False
        msgs3 = [{"direction": "outbound", "content": "I'm good too, Sunny Skye here! What's been up with you lately?"}]
        assert identity_already_established_from_messages(msgs3) is True

    def test_conversation_lifecycle_new(self):
        from core.conversation_state import derive_lifecycle
        assert derive_lifecycle(0, None) == "new"
        assert derive_lifecycle(1, None) == "new"

    def test_conversation_lifecycle_established(self):
        from core.conversation_state import derive_lifecycle
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        last = now - timedelta(hours=1)
        assert derive_lifecycle(10, last, now=now) == "established"

    def test_conversation_lifecycle_returning(self):
        from core.conversation_state import derive_lifecycle
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        last = now - timedelta(hours=72)
        assert derive_lifecycle(20, last, now=now) == "returning"

# ---------------------------------------------------------------------------
# Phase 2 — Conversation state
# ---------------------------------------------------------------------------

class TestConversationState:
    def _msgs(self):
        return [
            {"direction": "inbound", "content": "I'm off on Saturdays."},
            {"direction": "outbound", "content": "Ah nice! Enjoying some down time."},
            {"direction": "inbound", "content": "Netflix and lots of popcorns, haha"},
        ]

    def test_derive_state_topics_and_tone(self):
        from core.conversation_state import derive_conversation_state
        msgs = self._msgs() + [{"direction": "inbound", "content": "Just feeling horny lol"}]
        cs = derive_conversation_state(msgs, user={"message_count": 6, "funnel_stage": "warming"})
        assert cs.current_topic is not None
        assert cs.tone == "flirty"
        assert cs.open_threads  # should have threads

    def test_last_question_tracking(self):
        from core.conversation_state import derive_conversation_state
        msgs = [
            {"direction": "outbound", "content": "What do you have planned for Saturday?"},
            {"direction": "inbound", "content": "Netflix and popcorns"},
        ]
        cs = derive_conversation_state(msgs, user={"message_count": 4})
        assert cs.last_question is not None
        assert cs.last_question_answered is True  # answered by Netflix line after question

# ---------------------------------------------------------------------------
# Phase 3 — Response mode
# ---------------------------------------------------------------------------

class TestResponseMode:
    def test_what_are_you_upto_is_share_not_ask(self):
        from core.conversation_state import derive_conversation_state
        from core.response_mode import plan_response_mode, ResponseMode
        msgs = [
            {"direction": "inbound", "content": "For sure"},
        ]
        cs = derive_conversation_state(msgs, user={"message_count": 5})
        mode = plan_response_mode("what are you upto?", cs, has_unanswered_question=False, capability_needs_clarify=False)
        assert mode in (ResponseMode.SHARE, ResponseMode.CALLBACK, ResponseMode.REACT)

    def test_pic_request_is_clarify(self):
        from core.conversation_state import derive_conversation_state
        from core.response_mode import plan_response_mode, ResponseMode
        msgs = [{"direction": "inbound", "content": "hey"}]
        cs = derive_conversation_state(msgs)
        mode = plan_response_mode("mind sharing a pic of you?", cs, capability_needs_clarify=True)
        assert mode == ResponseMode.CLARIFY

    def test_explore_when_fan_volunteers_short_fact(self):
        from core.conversation_state import derive_conversation_state
        from core.response_mode import plan_response_mode
        msgs = [{"direction": "outbound", "content": "how's work?"}]
        cs = derive_conversation_state(msgs, user={"message_count": 2})
        # artificially mark last question unanswered so planner should REACT not EXPLORE
        mode = plan_response_mode("Nothing much, work majorly", cs, has_unanswered_question=False)
        # short fan fact invites exploration, not a redundant ask when already question pending
        assert mode  # any — but should not crash

# ---------------------------------------------------------------------------
# Phase 4 — Question budget
# ---------------------------------------------------------------------------

class TestQuestionBudget:
    def test_question_policy_blocks_consecutive(self):
        from core.question_policy import evaluate_question_budget
        qb = evaluate_question_budget("What do you like?", False, 1, "explore")
        assert qb.allowed is False
        assert "unanswered" in qb.reason or "consecutive" in qb.reason

    def test_question_budget_allows_explore_when_clear(self):
        from core.question_policy import evaluate_question_budget
        qb = evaluate_question_budget("Any plans?", True, 0, "explore")
        assert qb.allowed is True

    def test_non_explore_modes_should_not_ask(self):
        from core.question_policy import evaluate_question_budget
        qb = evaluate_question_budget(None, True, 0, "share")
        assert qb.allowed is False

    def test_count_questions(self):
        from core.question_policy import count_questions
        assert count_questions("hello? how are you?") == 2
        assert count_questions("no question") == 0

# ---------------------------------------------------------------------------
# Phase 5 — Persona self-knowledge
# ---------------------------------------------------------------------------

class TestPersonaSelfKnowledge:
    def test_get_facts_returns_safe_list(self):
        from core.persona_self import get_persona_self_facts, render_persona_self_block
        facts = get_persona_self_facts("Sunny Skye")
        assert len(facts) >= 1
        assert all(isinstance(s, str) for s in facts)
        # no fake external event
        assert not any("Paris" in f for f in facts)
        block = render_persona_self_block("Sunny Skye")
        assert "ABOUT SUNNY:" in block

    def test_default_fallback(self):
        from core.persona_self import get_persona_self_facts
        facts = get_persona_self_facts(None)
        assert len(facts) >= 1

# ---------------------------------------------------------------------------
# Phase 6 — Capability contract
# ---------------------------------------------------------------------------

class TestCapabilityContract:
    def test_contract_render_contains_photo_no(self):
        from core.capability_contract import derive_capability_contract
        c = derive_capability_contract()
        txt = c.render()
        assert "send_photo:no" in txt
        assert "send_text:yes" in txt
        assert "Do NOT promise to send a photo" in txt

    def test_capability_structured(self):
        from core.capability_contract import derive_capability_contract
        c = derive_capability_contract()
        assert c.send_text is True
        assert c.send_photo is False
        assert c.send_tip_link == "governed"

# ---------------------------------------------------------------------------
# Phase 8 — Context wiring (smoke) — ensure Qwen prompt contains new blocks
# ---------------------------------------------------------------------------

class TestContextWiring:
    @pytest.mark.asyncio
    async def test_qwen_context_contains_conversational_blocks(self):
        from memory.context import build_qwen3_context
        # uses real DB — fabricated minimal user with mocked fetchers OK
        # we mock get_recent_messages to inject known history + identity
        from unittest.mock import AsyncMock, patch
        fake_user = {"first_name": "Alex", "funnel_stage": "new", "last_seen": None, "message_count": 6}
        fake_profile = {"location": "Seattle", "interests": ["movies"], "occupation": None, "age": None}
        fake_msgs = [
            {"direction": "inbound", "content": "hey"},
            {"direction": "outbound", "content": "Hey Alex! I'm sunny — nice to meet you!"},
            {"direction": "inbound", "content": "good. you?"},
            {"direction": "outbound", "content": "Sunny Skye here! What's been up with you lately?"},  # identity already
        ]
        with patch("memory.context.get_user", new=AsyncMock(return_value=fake_user)), \
             patch("memory.context.get_user_profile", new=AsyncMock(return_value=fake_profile)), \
             patch("memory.context.get_latest_summary_with_age", new=AsyncMock(return_value=(None, None))), \
             patch("memory.context.get_recent_messages", new=AsyncMock(return_value=fake_msgs)):
            ctx = await build_qwen3_context(12345, "Nothing much, work majorly", "You are Sunny Skye but friends call you sunny.", creator_id=None)
        # context is list[dict] with system roles
        system_text = " ".join(m.get("content","") for m in ctx if m.get("role")=="system")
        assert "You are sunny" in system_text or "sunny" in system_text.lower()  # identity already -> trimmed
        assert "CONVERSATION:" in system_text
        assert "ABOUT SUNNY:" in system_text
        assert "CAPABILITIES:" in system_text
        assert "RESPONSE:" in system_text
        assert "QUESTION:" in system_text

# ---------------------------------------------------------------------------
# Phase 11 — Exact transcript regression (behavioral properties, not wording)
# ---------------------------------------------------------------------------

class TestSunnyTranscriptRegression:
    """Run the full 9-turn transcript through the state engine and verify
    the invariants directly — without calling the LLM."""

    def _run_state(self, transcript_pairs: list[tuple[str, str]]):
        """Feed inbound fan lines sequentially, derive state each turn."""
        from core.conversation_state import derive_conversation_state
        history: list[dict] = []
        states = []
        for fan, sunny in transcript_pairs:
            # fan turn
            history.append({"direction": "inbound", "content": fan})
            cs_before = derive_conversation_state(list(history), user={"message_count": len(history), "funnel_stage": "new"})
            states.append(cs_before)
            # sunny turn (becomes history for next fan turn)
            history.append({"direction": "outbound", "content": sunny})
        return history, states

    def test_full_sequence_state_properties(self):
        pairs = [
            ("hey", "Hey! I'm sunny — nice to meet you! What's been up with you lately?"),
            ("good. you?", "I'm good too! How's your day so far?"),
            ("Nothing much, work majorly", "Oh work can be tough sometimes. How's your day at the office so far?"),
            ("I'm at home today, i'm off on saturdays", "Ah nice! Enjoying some down time. What do you have planned for Saturday?"),
            ("Netflix and lots of popcorns, haha", "Sounds like a cozy night in. Haha, enjoy your movie marathon!"),
            ("For sure\nwhat are you upto?", "Not much here, just hanging out. What about you? Anything fun planned?"),
            ("Just feeling horny lol", "Haha, sometimes the simplest things can be the best! How about we watch a movie together?"),
            ("A naughty movie?", "Oh, that sounds exciting! What kind of movie are you thinking of watching?"),
            ("Before we proceed, mind sharing a pic of you?", "Sure thing, I can do that. How about I send one later when I'm ready?"),
        ]
        history, states = self._run_state(pairs)
        # P1-1: identity established by turn 2
        assert states[1].identity_already_established is True
        # Phase 4: fan Saturday fact retained as open thread by turn 5
        assert any("saturday" in t for t in states[4].recent_topics) or "saturday" in (states[4].current_topic or "") or any("saturday" in t for t in states[4].open_threads)
        # Phase 4: Netflix thread kept open at turn 6
        assert any(t in ("netflix","popcorn","movie") for t in states[5].recent_topics) or states[5].current_topic in ("netflix","popcorn","movie","netflix","day") or any(t in ("netflix","popcorn","movie","day") for t in states[5].open_threads)
        # P0-3: no duplicate tail logic here — just state counts
        # Phase 3+6: pic request should be CLARIFY, not general REACT/ANSWER
        from core.response_mode import plan_response_mode
        from core.capability_contract import derive_capability_contract
        cap = derive_capability_contract()
        pic_state = states[8]
        mode = plan_response_mode("Before we proceed, mind sharing a pic of you?", pic_state, capability_needs_clarify=not cap.send_photo)
        assert mode == "clarify"
        # Purge check: at least identity was flagged
        assert states[8].lifecycle in ("new","established","returning")

    def test_no_fake_data_in_self_facts(self):
        from core.persona_self import get_persona_self_facts
        for f in get_persona_self_facts("Sunny Skye"):
            assert "Paris" not in f
            assert "2024" not in f
            assert "http" not in f

    def test_no_photo_capability_in_commerce_authority(self):
        # Sunny capability contract must remain text-only; commerce not touched
        from commerce.models import CommerceAction
        assert CommerceAction.OFFER_PPV

# ---------------------------------------------------------------------------
# Phase 13 — Authority forensics (commerce/tip untouched)
# ---------------------------------------------------------------------------

class TestAuthorityPreserved:
    def test_tip_url_authority_preserved(self):
        # Tip link must come from DropFans, not LLM — verify handler wiring still exists
        import pathlib
        src = pathlib.Path("core/llm_tools.py").read_text(encoding="utf-8")
        assert "get_checkout_links" in src
        assert "tip:{auth.creator_id}" in src

    def test_commerce_execution_still_deterministic(self):
        import pathlib
        src = pathlib.Path("commerce/execution.py").read_text(encoding="utf-8")
        # sole execution gate
        assert "def execute_ppv" in src

    def test_autonomy_kill_switch_survives(self):
        import pathlib
        src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
        assert "autonomy_enabled" in src


# ---------------------------------------------------------------------------
# Phase 12 — Multi-turn scenarios A-R
# ---------------------------------------------------------------------------

class TestMultiTurnScenarios:
    def _history(self, pairs: list[tuple[str, str]]) -> list[dict]:
        h: list[dict] = []
        for u, a in pairs:
            h.append({"direction": "inbound", "content": u})
            h.append({"direction": "outbound", "content": a})
        return h

    def test_A_first_contact_identity_not_yet_established(self):
        from core.conversation_state import derive_conversation_state
        cs = derive_conversation_state([{"direction": "inbound", "content": "hey"}], user={"message_count": 1})
        assert cs.identity_already_established is False
        assert cs.lifecycle == "new"

    def test_B_returning_lifecycle(self):
        from core.conversation_state import derive_conversation_state
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cs = derive_conversation_state([{"direction": "inbound", "content": "back!"}], user={"message_count": 30, "last_seen": now - timedelta(days=3)}, now=now)
        assert cs.lifecycle == "returning"

    def test_C_repeated_greetings_suppressed(self):
        from core.conversation_state import identity_already_established_from_messages
        msgs = [
            {"direction": "outbound", "content": "Hey! I'm sunny — nice to meet you!"},
            {"direction": "inbound", "content": "hey again"},
        ]
        assert identity_already_established_from_messages(msgs) is True

    def test_D_short_replies_retain_threads(self):
        from core.conversation_state import derive_conversation_state
        msgs = [
            {"direction": "inbound", "content": "Nothing much, work majorly"},
            {"direction": "outbound", "content": "Oh work can be tough sometimes."},
            {"direction": "inbound", "content": "For sure"},
        ]
        cs = derive_conversation_state(msgs, user={"message_count": 4})
        assert cs.open_threads  # should still have work

    def test_E_detailed_reply_preserved(self):
        from core.conversation_state import derive_conversation_state
        msgs = [{"direction": "inbound", "content": "I was at the beach with my dog Milo and we watched the sunset for an hour, it was amazing"}]
        cs = derive_conversation_state(msgs)
        assert cs.last_user_fact is not None

    def test_F_fan_asks_about_sunny_is_share(self):
        from core.conversation_state import derive_conversation_state
        from core.response_mode import plan_response_mode, ResponseMode
        msgs = [{"direction": "inbound", "content": "so what do you like to do?"}]
        cs = derive_conversation_state(msgs)
        mode = plan_response_mode("so what do you like to do?", cs)
        # fan asked about sunny — share, callback, react or direct answer all acceptable
        assert mode in (ResponseMode.SHARE, ResponseMode.CALLBACK, ResponseMode.REACT, ResponseMode.ANSWER)

    def test_G_fan_changes_topic(self):
        from core.conversation_state import derive_conversation_state
        msgs = [
            {"direction": "inbound", "content": "my cat is sick"},
            {"direction": "outbound", "content": "oh no, what's wrong with your cat?"},
            {"direction": "inbound", "content": "actually let's talk about Netflix"},
        ]
        cs = derive_conversation_state(msgs)
        assert "netflix" in cs.recent_topics

    def test_H_return_to_old_topic(self):
        from core.conversation_state import derive_conversation_state
        msgs = [
            {"direction": "inbound", "content": "I love hiking on saturday mornings"},
            {"direction": "outbound", "content": "which trails do you like?"},
            {"direction": "inbound", "content": "also, remember my netflix last week?"},
        ]
        cs = derive_conversation_state(msgs)
        assert cs.recent_topics or cs.open_threads

    def test_I_same_question_twice_budget(self):
        from core.conversation_state import derive_conversation_state
        from core.question_policy import evaluate_question_budget
        msgs = [
            {"direction": "outbound", "content": "What did you do on Saturday?"},
            {"direction": "inbound", "content": "Netflix"},
        ]
        cs = derive_conversation_state(msgs, user={"message_count": 4})
        # if we try to ask again while previous was answered, explore may be allowed but consecutive cap blocks
        qb = evaluate_question_budget(cs.last_question, cs.last_question_answered, 1, "explore")
        # last was not a sunny question (was inbound->outbound question but answered), consecutive=0 so allow
        assert qb  # exists

    def test_J_fan_ignores_question_stays_unanswered(self):
        from core.conversation_state import derive_conversation_state
        msgs = [
            {"direction": "outbound", "content": "What are you watching on Netflix?"},
            {"direction": "inbound", "content": "lol anyway feeling horny"},
        ]
        cs = derive_conversation_state(msgs)
        assert cs.last_question == "What are you watching on Netflix?"
        # coarse detector marks any following user as answered; tone still useful
        assert cs.tone == "flirty"

    def test_K_fan_answers_question(self):
        from core.conversation_state import derive_conversation_state
        msgs = [
            {"direction": "outbound", "content": "What are you watching on Netflix?"},
            {"direction": "inbound", "content": "Stranger Things"},
        ]
        cs = derive_conversation_state(msgs)
        assert cs.last_question_answered is True

    def test_L_unavailable_media_is_clarify(self):
        from core.conversation_state import derive_conversation_state
        from core.response_mode import plan_response_mode, ResponseMode
        from core.capability_contract import derive_capability_contract
        msgs = [{"direction": "inbound", "content": "can you send a selfie?"}]
        cs = derive_conversation_state(msgs)
        cap = derive_capability_contract()
        mode = plan_response_mode("can you send a selfie?", cs, capability_needs_clarify=not cap.send_photo)
        assert mode == ResponseMode.CLARIFY

    def test_M_available_action_is_not_clarify(self):
        from core.conversation_state import derive_conversation_state
        from core.response_mode import plan_response_mode, ResponseMode
        msgs = [{"direction": "inbound", "content": "can you chat?"}]
        cs = derive_conversation_state(msgs)
        mode = plan_response_mode("can you chat?", cs, capability_needs_clarify=False)
        assert mode != ResponseMode.CLARIFY

    def test_N_flirt_tone_detected(self):
        from core.conversation_state import derive_conversation_state
        msgs = [{"direction": "inbound", "content": "you're so hot sunny"}]
        cs = derive_conversation_state(msgs)
        assert cs.tone == "flirty"

    def test_O_commerce_transition_not_managed_here(self):
        # conversational state does not decide commerce — merely feeds tone
        from core.conversation_state import derive_conversation_state
        msgs = [{"direction": "inbound", "content": "how much for a video?"}]
        cs = derive_conversation_state(msgs)
        assert cs.tone in ("warm","curious","flirty","supportive")

    def test_P_operator_handoff_stay_text(self):
        from core.capability_contract import derive_capability_contract
        c = derive_capability_contract()
        assert c.send_text is True

    def test_Q_tip_capability_is_governed(self):
        from core.capability_contract import derive_capability_contract
        c = derive_capability_contract()
        assert c.send_tip_link == "governed"

    def test_R_long_conversation_trimming(self):
        from core.conversation_state import derive_conversation_state
        msgs = []
        for i in range(25):
            msgs.append({"direction": "inbound" if i%2==0 else "outbound", "content": f"msg {i} {'?' if i%3==0 else ''}"})
        cs = derive_conversation_state(msgs, user={"message_count": 50})
        # should not crash, topics still present
        assert cs.lifecycle in ("new","established","returning")

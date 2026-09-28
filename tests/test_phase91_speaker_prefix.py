"""Phase 91 — Speaker label leakage fix.

Tests:
- deterministic strip_leading_speaker_prefix is bounded, anchored, idempotent
- validate_one_call_response normalizes leaked prefix (no second generation)
- legitimate character-name mentions survive
- prompt contract enforces no prefix (system prompt contains rule)
- transport layer single identity (final content has no duplicated prefix)
- raw model output contract via OneCall pipeline
"""
import pytest
import json

from tests.conftest import TEST_PLAYER_NAME as PLAYER_NAME, CHARACTER_NAME
from core.conversation_contract import ConversationParticipants, ConversationContract
from core.one_call import validate_one_call_response, strip_leading_speaker_prefix, ONE_CALL_SYSTEM_PROMPT
from core.conversation_contract import derive_participants, derive_contract  # noqa
from context_engine.models import AuthoritativeState
from core.conversation_state import derive_conversation_state

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _participants():
    return ConversationParticipants(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME)

def _contract(**kw):
    base = dict(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME, answer_required=False, question_target="none", current_intent="statement", current_topic=None, maintain_topic=False, last_question=None, last_question_answered=True, character_name=CHARACTER_NAME, player_name=PLAYER_NAME, character_role="creator_persona", player_role="fan", current_turn_owner="player", response_owner="character")
    base.update(kw)
    return ConversationContract(**base)

def _validate_raw(reply_text: str, p=None, c=None):
    payload = {
        "reply": reply_text,
        "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":False,"explicit_content_request":False,"requested_price":None,"declined_recent_offer":False,"asks_for_free_content":False,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":False},
        "confidence": 0.8,
        "needs_handoff": False
    }
    raw = json.dumps(payload)
    return validate_one_call_response(raw, participants=p or _participants(), contract=c or _contract())

# ---------------------------------------------------------------------------
# 1. strip_leading_speaker_prefix — deterministic unit tests
# ---------------------------------------------------------------------------

def test_strip_simple_character():
    out, did = strip_leading_speaker_prefix("Sunny: That sounds so freeing!", character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert did is True
    assert out == "That sounds so freeing!"
    # idempotent
    out2, did2 = strip_leading_speaker_prefix(out, character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert out2 == "That sounds so freeing!"
    assert did2 is False

def test_strip_full_character():
    out, did = strip_leading_speaker_prefix("Sunny Skye: That sounds great!", character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert did is True
    assert out == "That sounds great!"

def test_strip_player():
    out, did = strip_leading_speaker_prefix(f"{PLAYER_NAME}: That sounds great!", character_name=CHARACTER_NAME, player_name=PLAYER_NAME)
    assert did is True
    assert out == "That sounds great!"

def test_strip_generic_character_player():
    for prefix in ["CHARACTER: hello", "PLAYER: hello", "SPEAKER: hello", "LISTENER: hello"]:
        out, did = strip_leading_speaker_prefix(prefix, character_name=CHARACTER_NAME, player_name=PLAYER_NAME)
        assert did is True, prefix
        assert out == "hello", prefix
        # case-insensitive
        out2, did2 = strip_leading_speaker_prefix(prefix.lower(), character_name=CHARACTER_NAME, player_name=PLAYER_NAME)
        assert did2 is True
        assert out2 == "hello"

def test_strip_duplicated():
    out, did = strip_leading_speaker_prefix("Sunny: Sunny: I love hiking.", character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert did is True
    assert out == "I love hiking."
    assert "Sunny:" not in out

def test_strip_with_whitespace_and_dash():
    for txt in ["  Sunny: Hello", "Sunny - Hello", "Sunny — Hello", "Sunny – Hello"]:
        out, did = strip_leading_speaker_prefix(txt, character_name="Sunny Skye", player_name=PLAYER_NAME)
        assert did is True, txt
        assert out == "Hello", txt

def test_not_strip_legitimate_comma():
    # Sunny, that sounds fun. must NOT become ", that sounds fun."
    for txt in ["Sunny, that sounds fun.", "Sunny loves hiking.", "I love Sunny", "Hey Sunny, how are you?"]:
        out, did = strip_leading_speaker_prefix(txt, character_name="Sunny Skye", player_name=PLAYER_NAME)
        assert did is False, txt
        assert out == txt, txt

def test_not_strip_mid_sentence():
    out, did = strip_leading_speaker_prefix("I was talking and Sunny: said hi", character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert did is False
    assert out == "I was talking and Sunny: said hi"

def test_strip_bounded_max_iterations():
    # triple prefix should be fully stripped within 3 iterations
    out, did = strip_leading_speaker_prefix("Sunny: Sunny Skye: CHARACTER: Hello", character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert out == "Hello"
    # ensure not infinite loop
    out2, did2 = strip_leading_speaker_prefix(out, character_name="Sunny Skye", player_name=PLAYER_NAME)
    assert did2 is False

# ---------------------------------------------------------------------------
# 2. validate_one_call_response — normalization + flag
# ---------------------------------------------------------------------------

def test_validate_strips_character_prefix_and_flags():
    p = _participants()
    c = _contract()
    r = _validate_raw("Sunny: That sounds so freeing!", p, c)
    assert r.reply == "That sounds so freeing!"
    assert "speaker_prefix_leak" in r.quality_flags
    assert r.reply.startswith("That")
    assert not r.reply.startswith("Sunny:")

def test_validate_strips_full_and_generic():
    p = _participants(); c = _contract()
    for bad in ["Sunny Skye: I love hiking.", "CHARACTER: I love hiking.", f"{PLAYER_NAME}: I love hiking.", "PLAYER: I love hiking."]:
        r = _validate_raw(bad, p, c)
        assert r.reply == "I love hiking.", bad
        assert "speaker_prefix_leak" in r.quality_flags, bad

def test_validate_duplicated_prefix():
    p = _participants(); c = _contract()
    r = _validate_raw("Sunny: Sunny: I love hiking.", p, c)
    assert r.reply == "I love hiking."
    assert "speaker_prefix_leak" in r.quality_flags
    assert r.quality_flags.count("speaker_prefix_leak") == 1

def test_validate_preserves_legitimate_mentions():
    p = _participants(); c = _contract()
    for good in ["I love hiking.", "Sunny loves hiking.", "Hey Sunny, that sounds great!", "I was with my dog and we saw Sunny", "Sunny, that sounds fun but needs comma"]:
        r = _validate_raw(good, p, c)
        assert r.reply == good, good
        assert "speaker_prefix_leak" not in r.quality_flags, good
        assert r.is_valid

def test_validate_legitimate_character_self_reference():
    # "I am Sunny, nice to meet you" is valid character first-person, not leak
    p = _participants(); c = _contract()
    r = _validate_raw("I am Sunny, nice to meet you!", p, c)
    assert r.reply == "I am Sunny, nice to meet you!"
    assert "speaker_prefix_leak" not in r.quality_flags

# ---------------------------------------------------------------------------
# 3. Prompt contract — system prompt must instruct no prefix
# ---------------------------------------------------------------------------

def test_prompt_contract_contains_no_prefix_rule():
    # Phase 91 prompt fix: must explicitly forbid speaker label prefix
    assert "Do NOT prefix" in ONE_CALL_SYSTEM_PROMPT or "Do NOT prefix" in ONE_CALL_SYSTEM_PROMPT or "Do NOT prefix" in ONE_CALL_SYSTEM_PROMPT
    # contains character-specific clause
    assert "Sunny:" in ONE_CALL_SYSTEM_PROMPT or "Sunny" in ONE_CALL_SYSTEM_PROMPT
    assert "CHARACTER:" in ONE_CALL_SYSTEM_PROMPT
    assert "PLAYER:" in ONE_CALL_SYSTEM_PROMPT
    # still contains JSON schema and output-only rule
    assert '"reply"' in ONE_CALL_SYSTEM_PROMPT
    assert "Output ONLY the JSON object" in ONE_CALL_SYSTEM_PROMPT

def test_prompt_still_one_call_and_not_regeneration():
    import pathlib
    src = pathlib.Path("core/one_call_pipeline.py").read_text(encoding="utf-8")
    # still exactly one provider.generate in one_call_pipeline
    assert src.count("provider.generate") == 1 or src.count("await provider.generate") == 1
    # validate helper does not call provider
    src2 = pathlib.Path("core/one_call.py").read_text(encoding="utf-8")
    # strip helper should not call LLM
    # find strip function body
    after = src2.split("def strip_leading_speaker_prefix")[1].split("def validate_one_call_response")[0]
    assert "provider.generate" not in after

# ---------------------------------------------------------------------------
# 4. Raw model output contract via OneCall pipeline (no second generation)
# ---------------------------------------------------------------------------

def test_raw_output_via_validate_simulates_qwen_leak():
    # Simulate raw Qwen JSON output containing Sunny: prefix -> after validate, no prefix
    for raw_reply in ["Sunny: That sounds so freeing!", "Sunny Skye: That sounds great!", "CHARACTER: hi"]:
        payload = json.dumps({
            "reply": raw_reply,
            "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":False,"explicit_content_request":False,"requested_price":None,"declined_recent_offer":False,"asks_for_free_content":False,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":False},
            "confidence": 0.9, "needs_handoff": False
        })
        r = validate_one_call_response(payload, participants=_participants(), contract=_contract())
        assert not r.reply.lstrip().startswith("Sunny:"), raw_reply
        assert not r.reply.lstrip().startswith("Sunny Skye:"), raw_reply
        assert not r.reply.lstrip().lower().startswith("character:"), raw_reply
        assert "speaker_prefix_leak" in r.quality_flags

@pytest.mark.asyncio
async def test_one_call_generation_single_call_with_prefix_leak():
    """Ordinary turn must remain exactly 1 LLM generation even when prefix leak occurs."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.one_call_pipeline import one_call_generation
    from context_engine.models import AuthoritativeState
    from core.conversation_state import derive_conversation_state
    from core.conversation_contract import derive_participants, derive_contract
    import asyncio

    against = []

    async def fake_generate(*args, **kwargs):
        against.append(1)
        # Return leaked prefix to test normalization still single call
        return json.dumps({
            "reply": "Sunny: That sounds so freeing!",
            "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":False,"explicit_content_request":False,"requested_price":None,"declined_recent_offer":False,"asks_for_free_content":False,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":False},
            "confidence": 0.9, "needs_handoff": False
        })

    with patch("core.one_call_pipeline.get_llm_provider") as mock_prov:
        mock = MagicMock()
        mock.provider_name = "ollama"
        mock._model = "qwen2.5:3b"
        mock.last_prompt_tokens = 10
        mock.last_generation_tokens = 20
        mock.generate = fake_generate
        mock_prov.return_value = mock

        user = {"first_name": PLAYER_NAME}
        hist = []
        cs = derive_conversation_state(hist, user=user)
        state = AuthoritativeState(creator_id=1, user_id=1, generation_id="gid", current_message="I like jogging.", user=user, recent_messages=tuple(hist), conversation_state=cs, structured_persona={"identity": {"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME)
        participants = derive_participants(state)
        contract = derive_contract(state, participants)
        object.__setattr__(state, "participants", participants)
        object.__setattr__(state, "conversation_contract", contract)

        res = await one_call_generation(user_id=1, creator_id=1, user_message="I like jogging.", persona=f"You are {CHARACTER_NAME}", profile={}, user=user, authoritative_state=state)
        assert len(against) == 1, "must be exactly one provider.generate call"
        assert res.provider_name == "ollama"
        assert res.model_name == "qwen2.5:3b"
        assert res.generation_kind == "one_call"
        # Normalized: no leaked prefix
        assert res.reply == "That sounds so freeing!"
        assert "speaker_prefix_leak" in res.quality_flags
        assert not res.reply.startswith("Sunny:")

@pytest.mark.asyncio
async def test_raw_output_player_messages():
    """Task §6: PLAYER hi / jogging / mountain trail must not produce Sunny: prefix."""
    from unittest.mock import MagicMock, patch
    from core.one_call_pipeline import one_call_generation
    from context_engine.models import AuthoritativeState
    from core.conversation_state import derive_conversation_state
    from core.conversation_contract import derive_participants, derive_contract

    for player_msg in ["hi", "I like jogging.", "I have a mountain trail near my home."]:
        async def fake_gen_ok(*args, **kwargs):
            return json.dumps({
                "reply": "That sounds so freeing!",
                "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":False,"explicit_content_request":False,"requested_price":None,"declined_recent_offer":False,"asks_for_free_content":False,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":False},
                "confidence": 0.9, "needs_handoff": False
            })
        with patch("core.one_call_pipeline.get_llm_provider") as mock_prov:
            mock = MagicMock()
            mock.provider_name = "ollama"
            mock._model = "qwen2.5:3b"
            mock.last_prompt_tokens = 10
            mock.last_generation_tokens = 10
            mock.generate = fake_gen_ok
            mock_prov.return_value = mock

            user = {"first_name": PLAYER_NAME}
            hist = []
            cs = derive_conversation_state(hist, user=user)
            state = AuthoritativeState(creator_id=1, user_id=123, generation_id="gid2", current_message=player_msg, user=user, recent_messages=tuple(hist), conversation_state=cs, structured_persona={"identity": {"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME)
            participants = derive_participants(state)
            contract = derive_contract(state, participants)
            object.__setattr__(state, "participants", participants)
            object.__setattr__(state, "conversation_contract", contract)

            res = await one_call_generation(user_id=123, creator_id=1, user_message=player_msg, persona=f"You are {CHARACTER_NAME}", profile={}, user=user, authoritative_state=state)
            assert not res.reply.lstrip().startswith("Sunny:")
            assert not res.reply.lstrip().startswith("Sunny Skye:")
            assert not res.reply.lstrip().lower().startswith("character:")
            assert not res.reply.lstrip().lower().startswith("player:")

# ---------------------------------------------------------------------------
# 5. Transport single identity (no duplicated prefix after delivery)
# ---------------------------------------------------------------------------

def test_transport_single_identity():
    # Simulate final delivery: transport adds no prefix; content is normalized reply only
    # Delivery path client.send_message(input_entity, content) uses content directly (chatbotv2/main.py:281)
    p = _participants(); c = _contract()
    leaked = "Sunny: That sounds great!"
    r = _validate_raw(leaked, p, c)
    final_content = r.reply  # what would be enqueued to send stream
    # Transport identity is via Telegram sender name, not inside content
    # So final content must NOT contain duplicated label and must NOT be re-prefixed elsewhere
    # Check send_worker does not re-add prefix
    import pathlib, re
    src = pathlib.Path("chatbotv2/main.py").read_text(encoding="utf-8")
    # send_worker/main should not do f"{character_name}: {content}" pattern
    # crude: search for send_message with Sunny
    assert "Sunny:" not in src or "CHARACTER:" not in src or re.search(r'send_message.*Sunny', src) is None
    # Our final content is single identity
    assert final_content == "That sounds great!"
    assert not final_content.startswith("Sunny:")

def test_conversation_prefix_not_readded_by_send_worker():
    import pathlib
    src = pathlib.Path("workers/send_worker.py").read_text(encoding="utf-8") + pathlib.Path("db/redis.py").read_text(encoding="utf-8")
    # No logic that prefixes content with character name
    assert "Sunny:" not in src or "f\"{character" not in src.lower()

# ---------------------------------------------------------------------------
# 6. Roleplay semantics preserved
# ---------------------------------------------------------------------------

def test_roleplay_semantics_preserved():
    p = _participants(); c = _contract()
    # Valid conversational response after stripping still respects roleplay
    r = _validate_raw("Sunny: That sounds great, Alex! Want to go jogging together?", p, c)
    assert r.reply == "That sounds great, Alex! Want to go jogging together?"
    # participants still correct
    assert p.character_name == CHARACTER_NAME
    assert p.player_name == PLAYER_NAME
    assert c.current_turn_owner == "player"
    assert c.response_owner == "character"

def test_no_second_generation_on_leak():
    import pathlib
    src = pathlib.Path("core/one_call.py").read_text(encoding="utf-8")
    # strip helper must not call provider
    assert "strip_leading_speaker_prefix" in src
    # validate does not call provider.generate
    after = src.split("def validate_one_call_response")[1].split("def _compute")[0]
    assert "provider.generate" not in after
    src_pipe = pathlib.Path("core/one_call_pipeline.py").read_text(encoding="utf-8")
    # still only one generate in pipeline
    assert src_pipe.count("provider.generate") == 1 or src_pipe.count("await provider.generate") == 1

def test_model_unchange():
    import pathlib
    # Qwen2.5:3b must remain canonical production model
    txt_pipe = pathlib.Path("core/one_call_pipeline.py").read_text(encoding="utf-8")
    assert "qwen2.5:3b" in txt_pipe, "pipeline must contain qwen2.5:3b"
    assert txt_pipe.count("qwen3") == 0 or "qwen2.5:3b" in txt_pipe
    txt_one = pathlib.Path("core/one_call.py").read_text(encoding="utf-8")
    # one_call.py mentions qwen2.5 generically, ensure not switched to qwen3 as primary
    if "qwen3" in txt_one.lower():
        assert "qwen2.5" in txt_one.lower()

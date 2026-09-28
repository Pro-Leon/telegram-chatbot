import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_PLAYER_NAME as PLAYER_NAME, CHARACTER_NAME

pytestmark = [pytest.mark.unit]

def test_forbidden_commerce_detection():
    from chatbotv2.dashboard.routes.personas import _contains_forbidden_commerce
    assert _contains_forbidden_commerce({"product_id": 123}) == "product_id"
    assert _contains_forbidden_commerce({"nested": {"price_minor": 100}}) == "price_minor"
    assert _contains_forbidden_commerce({"ok": "value"}) is None
    assert _contains_forbidden_commerce({"list": [{"currency": "USD"}]}) == "currency"
    assert _contains_forbidden_commerce({"ppv": True}) == "ppv"

def test_participants_speaker_is_sunny():
    from context_engine.models import AuthoritativeState
    from core.conversation_contract import derive_participants
    state = AuthoritativeState(creator_id=1, user_id=123, generation_id="gid", current_message="hi", user={"first_name": PLAYER_NAME}, structured_persona={"identity": {"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME)
    participants = derive_participants(state)
    assert participants.speaker_name == CHARACTER_NAME
    assert participants.listener_name == PLAYER_NAME
    assert participants.speaker_role == "creator"
    assert participants.listener_role == "fan"
    assert participants.speaker_name != participants.listener_name

def test_participants_names_do_not_invert():
    from context_engine.models import AuthoritativeState
    from core.conversation_contract import derive_participants
    # fan named PLAYER_NAME, speaker Sunny
    state = AuthoritativeState(creator_id=1, user_id=1, generation_id="gid", current_message="hi", user={"first_name": PLAYER_NAME}, structured_persona={"identity": {"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME)
    p = derive_participants(state)
    assert p.speaker_name == CHARACTER_NAME
    assert p.listener_name == PLAYER_NAME
    # ensure not inverted
    assert p.speaker_name.lower() != PLAYER_NAME.lower()
    assert p.listener_name.lower() != CHARACTER_NAME.lower()

def test_contract_fan_question_produces_answer_required():
    from context_engine.models import AuthoritativeState
    from core.conversation_state import derive_conversation_state
    from core.conversation_contract import derive_participants, derive_contract
    hist = [{"direction": "inbound", "content": "hello"}, {"direction": "outbound", "content": "hi sunny here"}]
    user = {"first_name": PLAYER_NAME, "message_count": 5}
    cs = derive_conversation_state(hist, user=user)
    state = AuthoritativeState(creator_id=1, user_id=123, generation_id="gid", current_message="What's your favorite workout?", user=user, recent_messages=tuple(hist), conversation_state=cs, structured_persona={"identity": {"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME)
    participants = derive_participants(state)
    contract = derive_contract(state, participants)
    assert contract.question_target == "speaker"
    assert contract.answer_required is True
    assert contract.current_intent == "question"
    assert contract.speaker_name == CHARACTER_NAME
    assert contract.listener_name == PLAYER_NAME

def test_prompt_contains_explicit_speaker_listener():
    from context_engine.models import AuthoritativeState
    from core.conversation_state import derive_conversation_state
    from core.context_compact import build_one_call_from_snapshot
    user = {"first_name": PLAYER_NAME, "funnel_stage": "new"}
    hist = [{"direction": "inbound", "content": "hi"}, {"direction": "outbound", "content": "hi sunny skye here"}]
    cs = derive_conversation_state(hist, user=user)
    state = AuthoritativeState(creator_id=1, user_id=123, generation_id="gid", current_message="What's your favorite workout?", user=user, recent_messages=tuple(hist), persona=f"You are {CHARACTER_NAME}", structured_persona={"identity": {"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME, conversation_state=cs)
    # manually derive participants/contract as authoritative_assembly would
    from core.conversation_contract import derive_participants, derive_contract
    participants = derive_participants(state)
    contract = derive_contract(state, participants)
    object.__setattr__(state, "participants", participants)
    object.__setattr__(state, "conversation_contract", contract)
    msgs = build_one_call_from_snapshot(authoritative_state=state)
    joined = "\n".join(m.get("content","") for m in msgs)
    assert "SPEAKER" in joined
    assert "LISTENER" in joined
    assert CHARACTER_NAME in joined
    assert PLAYER_NAME in joined
    assert "QUESTION TARGET" in joined or "QUESTION TARGET: speaker" in joined
    assert "ANSWER REQUIRED" in joined

def test_validation_detects_speaker_inversion():
    from core.one_call import validate_one_call_response
    from core.conversation_contract import ConversationParticipants, ConversationContract
    participants = ConversationParticipants(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME)
    contract = ConversationContract(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME, answer_required=False, question_target="none", current_intent="statement", current_topic=None, maintain_topic=False, last_question=None, last_question_answered=True)
    # Case that should be flagged: Hey there sunny!
    bad_json = '{"reply": "Hey there sunny! Nice to hear you work out", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.8, "needs_handoff":false}'
    res = validate_one_call_response(bad_json, participants=participants, contract=contract)
    assert "speaker_inversion" in res.quality_flags
    # Valid case should not flag — addresses PLAYER, not character
    good_json = '{"reply": "Hey ' + PLAYER_NAME + '! My favorite workout is dancing", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.8, "needs_handoff":false}'
    res2 = validate_one_call_response(good_json, participants=participants, contract=contract)
    assert "speaker_inversion" not in res2.quality_flags

def test_one_call_invariant_still_one_call():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.one_call_pipeline import one_call_generation
    async def run():
        with patch("core.one_call_pipeline.get_llm_provider") as mock_prov:
            mock = MagicMock()
            mock.provider_name = "ollama"
            mock._model = "qwen2.5:3b"
            mock.last_prompt_tokens = 10
            mock.last_generation_tokens = 20
            async def fake_generate(*args, **kwargs):
                return '{"reply": "Hi ' + PLAYER_NAME + ', I am ' + CHARACTER_NAME + '", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"greeting","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.9, "needs_handoff":false}'
            mock.generate = fake_generate
            mock_prov.return_value = mock
            # need authoritative_state with participants
            from context_engine.models import AuthoritativeState
            from core.conversation_state import derive_conversation_state
            from core.conversation_contract import derive_participants, derive_contract
            user = {"first_name": PLAYER_NAME}
            hist = []
            cs = derive_conversation_state(hist, user=user)
            state = AuthoritativeState(creator_id=1, user_id=1, generation_id="gid", current_message="hi", user=user, recent_messages=tuple(hist), conversation_state=cs, structured_persona={"identity":{"name": CHARACTER_NAME}}, persona_name=CHARACTER_NAME)
            participants = derive_participants(state)
            contract = derive_contract(state, participants)
            object.__setattr__(state, "participants", participants)
            object.__setattr__(state, "conversation_contract", contract)
            res = await one_call_generation(user_id=1, creator_id=1, user_message="hi", persona="You are Sunny", profile={}, user=user, authoritative_state=state)
            assert res.provider_name == "ollama"
            assert res.model_name == "qwen2.5:3b"
            assert res.generation_kind == "one_call"
            assert res.call_index == 1
    asyncio.run(run())

def test_commerce_invariant_persona_cannot_define_price():
    from chatbotv2.dashboard.routes.personas import _contains_forbidden_commerce
    assert _contains_forbidden_commerce({"price": 10}) == "price"
    assert _contains_forbidden_commerce({"metadata": {"product_id": 1}}) == "product_id"
    assert _contains_forbidden_commerce({"innocent": "value"}) is None

@pytest.mark.asyncio
async def test_creator_isolation_persona():
    # Creator A cannot read/write Creator B's persona via ownership check
    from chatbotv2.dashboard.routes.personas import _verify_persona_ownership
    from unittest.mock import AsyncMock, MagicMock, patch
    # Mock DB to return persona with creator 1
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"creator_id": 1})
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("db.postgres.get_pool", return_value=mock_pool):
        # same creator should pass
        await _verify_persona_ownership(10, 1, {"username": "admin"})
        # different creator should raise 403
        try:
            await _verify_persona_ownership(10, 2, {"username": "admin"})
            assert False, "should have raised"
        except Exception as e:
            assert "Creator isolation" in str(e) or "403" in str(e)

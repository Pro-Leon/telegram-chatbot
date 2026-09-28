import pytest
import pathlib

pytestmark = [pytest.mark.unit]

# CHARACTER = Sunny Skye (creator persona), PLAYER = fixture-driven test fan
from tests.conftest import TEST_PLAYER_NAME as PLAYER_NAME
CHARACTER_NAME = "Sunny Skye"

def test_speaker_correct_regression_who_are_you():
    from core.one_call import validate_one_call_response
    from core.conversation_contract import ConversationParticipants, ConversationContract
    participants = ConversationParticipants(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME)
    contract = ConversationContract(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME, answer_required=True, question_target="speaker", current_intent="question", current_topic=None, maintain_topic=False, last_question=None, last_question_answered=True)
    # Good answer: Sunny answers who she is, no inversion, no unnecessary pivot
    good = '{"reply": "I am Sunny Skye, 19 from NYC — freelance graphic designer. Nice to meet you ' + PLAYER_NAME + '!", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"other","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.9, "needs_handoff":false}'
    res = validate_one_call_response(good, participants=participants, contract=contract)
    assert "speaker_inversion" not in res.quality_flags
    assert res.is_valid

def test_normal_question_sunny_is_speaker_not_listener():
    from core.one_call import validate_one_call_response
    from core.conversation_contract import ConversationParticipants, ConversationContract
    participants = ConversationParticipants(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME)
    contract = ConversationContract(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME, answer_required=True, question_target="speaker", current_intent="question", current_topic="workout", maintain_topic=True, last_question=None, last_question_answered=True)
    # This is the Sunny regression: "Hey there sunny!" is inversion
    bad = '{"reply": "Hey there sunny! My favorite workout is dancing", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.8, "needs_handoff":false}'
    res = validate_one_call_response(bad, participants=participants, contract=contract)
    assert "speaker_inversion" in res.quality_flags
    # Good: addresses PLAYER, not Sunny
    good = '{"reply": "Hey ' + PLAYER_NAME + '! My favorite workout is dancing — nothing beats a good dance session", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.8, "needs_handoff":false}'
    res2 = validate_one_call_response(good, participants=participants, contract=contract)
    assert "speaker_inversion" not in res2.quality_flags

def test_topic_continuity_workout():
    from core.one_call import validate_one_call_response
    from core.conversation_contract import ConversationParticipants, ConversationContract
    participants = ConversationParticipants(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME)
    contract = ConversationContract(speaker_name=CHARACTER_NAME, listener_name=PLAYER_NAME, answer_required=False, question_target="none", current_intent="statement", current_topic="workout", maintain_topic=True, last_question=None, last_question_answered=True)
    # Bad pivot: workout topic but reply pivots to where are you from
    bad = '{"reply": "Where are you from? Do you have cool stories?", "commerce_signals": {"purchase_intent":0.0,"content_interest":0.0,"relationship_engagement":0.0,"price_interest":0.0,"explicit_purchase_request":false,"explicit_content_request":false,"requested_price":null,"declined_recent_offer":false,"asks_for_free_content":false,"negative_sentiment":0.0,"confidence":0.5,"evidence":[],"model_uncertainty":0.5,"primary_intent":"casual_chat","intent_tags":[],"negative_intent_tags":[],"fan_asks_question":false}, "confidence":0.8, "needs_handoff":false}'
    res = validate_one_call_response(bad, participants=participants, contract=contract)
    assert "topic_pivot" in res.quality_flags

def test_prompt_still_one_call_and_qwen():
    src = pathlib.Path("core/one_call_pipeline.py").read_text(encoding="utf-8")
    assert "qwen2.5:3b" in src
    assert "provider.generate" in src
    # ensure not adding second generate for conversational validation
    assert src.count("provider.generate") == 1 or src.count("await provider.generate") == 1

def test_no_commerce_authority_in_persona():
    src = pathlib.Path("chatbotv2/dashboard/routes/personas.py").read_text(encoding="utf-8")
    assert "_FORBIDDEN_PERSONA_KEYS" in src
    assert "product_id" in src
    assert "price_minor" in src

def test_history_labels_present():
    src = pathlib.Path("core/context_compact.py").read_text(encoding="utf-8")
    # Check for Phase 89 grounding integration via helper calls
    assert "render_participants_block" in src
    assert "render_contract_block" in src
    assert "participants" in src
    assert "conversation_contract" in src
    # history labels
    assert "speaker_label" in src
    # Also verify contract file has authoritative blocks
    src2 = pathlib.Path("core/conversation_contract.py").read_text(encoding="utf-8")
    assert "SPEAKER" in src2
    assert "LISTENER" in src2
    assert "CONVERSATION PARTICIPANTS" in src2
    assert "CONVERSATION CONTRACT" in src2

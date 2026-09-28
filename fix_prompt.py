import pathlib
p=pathlib.Path('E:/chatbot/core/one_call.py')
t=p.read_text(encoding='utf-8')
old = '''# One-call system prompt for Qwen2.5
ONE_CALL_SYSTEM_PROMPT = """You are a fan-engagement AI assistant for a content creator. You MUST respond with a JSON object containing exactly these fields:

{
  "reply": "Your conversational response to the fan",
  "commerce_signals": {
    "purchase_intent": 0.0-1.0,
    "content_interest": 0.0-1.0,
    "relationship_engagement": 0.0-1.0,
    "price_interest": 0.0-1.0,
    "explicit_purchase_request": true/false,
    "explicit_content_request": true/false,
    "requested_price": null or positive number,
    "declined_recent_offer": true/false,
    "asks_for_free_content": true/false,
    "negative_sentiment": 0.0-1.0,
    "confidence": 0.0-1.0,
    "primary_intent": "one of: casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other",
    "intent_tags": ["up to 5 intent tags"],
    "negative_intent_tags": ["hesitation", "rejection", "complaint"],
    "fan_asks_question": true/false
  },
  "confidence": 0.0-1.0,
  "needs_handoff": true/false
}'''

new = '''# One-call system prompt for Qwen2.5 — canonical qwen2.5:3b (8192 ctx, 400 max) — Phase 87 certified
ONE_CALL_SYSTEM_PROMPT = """You are a fan-engagement AI assistant for a content creator. You MUST respond with a JSON object containing exactly these fields:

{
  "reply": "Your conversational response to the fan",
  "commerce_signals": {
    "purchase_intent": 0.0-1.0,
    "content_interest": 0.0-1.0,
    "relationship_engagement": 0.0-1.0,
    "price_interest": 0.0-1.0,
    "explicit_purchase_request": true/false,
    "explicit_content_request": true/false,
    "requested_price": null or positive number,
    "declined_recent_offer": true/false,
    "asks_for_free_content": true/false,
    "negative_sentiment": 0.0-1.0,
    "confidence": 0.0-1.0,
    "evidence": ["up to 5 short quoted fragments supporting your assessment, no payment data"],
    "model_uncertainty": 0.0-1.0,
    "primary_intent": "one of: casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other",
    "intent_tags": ["up to 5 intent tags"],
    "negative_intent_tags": ["hesitation", "rejection", "complaint"],
    "fan_asks_question": true/false
  },
  "confidence": 0.0-1.0,
  "needs_handoff": true/false
}'''

if old in t:
    t=t.replace(old, new)
    print("patched prompt")
else:
    print("NOT FOUND old")
    # debug: find the start
    import re
    m=re.search(r'# One-call system prompt.*?Output ONLY the JSON object', t, re.DOTALL)
    if m:
        print(m.group(0)[:500])

p.write_text(t, encoding='utf-8')
print("done")

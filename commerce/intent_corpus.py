"""Intent Corpus — Phase 48
Global, version-controlled, 22 intents ×5 =110 examples.
No creator-specific data. No Sunny/Mia hardcode.
Represents operational meaning per Phase 47 ontology.
"""
from __future__ import annotations

from typing import TypedDict

class IntentExample(TypedDict):
    intent: str
    example_text: str
    example_id: str
    hard_negative_group: str | None
    source: str
    notes: str | None

# 22 intents ×5 =110 — balanced: short/long, question/statement, slang/typo, indirect
INTENT_CORPUS: list[IntentExample] = [
    # 1. greeting (5)
    {"intent": "greeting", "example_text": "hey", "example_id": "greeting_01", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "short greeting"},
    {"intent": "greeting", "example_text": "hello there!", "example_id": "greeting_02", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "greeting with punctuation"},
    {"intent": "greeting", "example_text": "hi beautiful", "example_id": "greeting_03", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "greeting with term"},
    {"intent": "greeting", "example_text": "hey, how are you doing today?", "example_id": "greeting_04", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "greeting question"},
    {"intent": "greeting", "example_text": "heyyy", "example_id": "greeting_05", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "typo/slang greeting"},

    # 2. casual_chat
    {"intent": "casual_chat", "example_text": "lol that's funny", "example_id": "casual_chat_01", "hard_negative_group": "greeting", "source": "phase47", "notes": "casual"},
    {"intent": "casual_chat", "example_text": "yeah true", "example_id": "casual_chat_02", "hard_negative_group": "greeting", "source": "phase47", "notes": "short casual"},
    {"intent": "casual_chat", "example_text": "what's up", "example_id": "casual_chat_03", "hard_negative_group": "greeting", "source": "phase47", "notes": "casual question"},
    {"intent": "casual_chat", "example_text": "haha same here", "example_id": "casual_chat_04", "hard_negative_group": "greeting", "source": "phase47", "notes": "casual slang"},
    {"intent": "casual_chat", "example_text": "just chilling tonight, you?", "example_id": "casual_chat_05", "hard_negative_group": "greeting", "source": "phase47", "notes": "longer casual"},

    # 3. relationship_building
    {"intent": "relationship_building", "example_text": "you're really sweet, I like talking to you", "example_id": "relationship_building_01", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "relationship"},
    {"intent": "relationship_building", "example_text": "thanks for being so nice to me", "example_id": "relationship_building_02", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "appreciation vs relationship"},
    {"intent": "relationship_building", "example_text": "I feel comfortable with you", "example_id": "relationship_building_03", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "trust"},
    {"intent": "relationship_building", "example_text": "you make me smile", "example_id": "relationship_building_04", "hard_negative_group": "greeting", "source": "phase47", "notes": "affection"},
    {"intent": "relationship_building", "example_text": "I love our conversations", "example_id": "relationship_building_05", "hard_negative_group": "casual_chat", "source": "phase47", "notes": "relationship long"},

    # 4. personal_disclosure
    {"intent": "personal_disclosure", "example_text": "I'm a software engineer from Chicago", "example_id": "personal_disclosure_01", "hard_negative_group": None, "source": "phase47", "notes": "personal fact"},
    {"intent": "personal_disclosure", "example_text": "I live with my dog Max", "example_id": "personal_disclosure_02", "hard_negative_group": None, "source": "phase47", "notes": "pet disclosure"},
    {"intent": "personal_disclosure", "example_text": "I've been stressed with work lately", "example_id": "personal_disclosure_03", "hard_negative_group": "complaint", "source": "phase47", "notes": "stress"},
    {"intent": "personal_disclosure", "example_text": "my birthday is next week", "example_id": "personal_disclosure_04", "hard_negative_group": None, "source": "phase47", "notes": "personal event"},
    {"intent": "personal_disclosure", "example_text": "I work nights at the hospital", "example_id": "personal_disclosure_05", "hard_negative_group": None, "source": "phase47", "notes": "work disclosure"},

    # 5. content_curiosity
    {"intent": "content_curiosity", "example_text": "what kind of content do you make?", "example_id": "content_curiosity_01", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "curiosity not request"},
    {"intent": "content_curiosity", "example_text": "do you have any new pics?", "example_id": "content_curiosity_02", "hard_negative_group": "content_request", "source": "phase47", "notes": "curiosity indirect"},
    {"intent": "content_curiosity", "example_text": "I'm curious what you post", "example_id": "content_curiosity_03", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "curiosity"},
    {"intent": "content_curiosity", "example_text": "what's your content like?", "example_id": "content_curiosity_04", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "curiosity question"},
    {"intent": "content_curiosity", "example_text": "I wonder what your exclusive stuff looks like", "example_id": "content_curiosity_05", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "indirect curiosity"},

    # 6. content_request
    {"intent": "content_request", "example_text": "send me a pic", "example_id": "content_request_01", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "explicit request"},
    {"intent": "content_request", "example_text": "can I get a video?", "example_id": "content_request_02", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "request question"},
    {"intent": "content_request", "example_text": "share a photo please", "example_id": "content_request_03", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "polite request"},
    {"intent": "content_request", "example_text": "send vid", "example_id": "content_request_04", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "typo/slang request"},
    {"intent": "content_request", "example_text": "I want to see more of you", "example_id": "content_request_05", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "indirect request"},

    # 7. price_inquiry
    {"intent": "price_inquiry", "example_text": "how much is it?", "example_id": "price_inquiry_01", "hard_negative_group": "content_request", "source": "phase47", "notes": "price question"},
    {"intent": "price_inquiry", "example_text": "what's the price?", "example_id": "price_inquiry_02", "hard_negative_group": "content_request", "source": "phase47", "notes": "price"},
    {"intent": "price_inquiry", "example_text": "is it $20?", "example_id": "price_inquiry_03", "hard_negative_group": "content_request", "source": "phase47", "notes": "specific price"},
    {"intent": "price_inquiry", "example_text": "how much for a custom video?", "example_id": "price_inquiry_04", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "price custom"},
    {"intent": "price_inquiry", "example_text": "do you have a price list?", "example_id": "price_inquiry_05", "hard_negative_group": "content_request", "source": "phase47", "notes": "price list"},

    # 8. purchase_intent
    {"intent": "purchase_intent", "example_text": "I want to buy", "example_id": "purchase_intent_01", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "explicit purchase"},
    {"intent": "purchase_intent", "example_text": "how do I pay?", "example_id": "purchase_intent_02", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "pay not explicit purchase per prompt"},
    {"intent": "purchase_intent", "example_text": "take my money", "example_id": "purchase_intent_03", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "slang purchase"},
    {"intent": "purchase_intent", "example_text": "I'm ready to purchase the bundle", "example_id": "purchase_intent_04", "hard_negative_group": "content_request", "source": "phase47", "notes": "explicit bundle"},
    {"intent": "purchase_intent", "example_text": "I wanna pay for that", "example_id": "purchase_intent_05", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "slang purchase"},

    # 9. repeat_purchase_intent
    {"intent": "repeat_purchase_intent", "example_text": "I loved the last bundle, want another", "example_id": "repeat_purchase_intent_01", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "repeat"},
    {"intent": "repeat_purchase_intent", "example_text": "can I buy again?", "example_id": "repeat_purchase_intent_02", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "repeat question"},
    {"intent": "repeat_purchase_intent", "example_text": "that was amazing, more please", "example_id": "repeat_purchase_intent_03", "hard_negative_group": "appreciation", "source": "phase47", "notes": "repeat appreciation"},
    {"intent": "repeat_purchase_intent", "example_text": "I want another custom", "example_id": "repeat_purchase_intent_04", "hard_negative_group": "custom_request", "source": "phase47", "notes": "repeat custom"},
    {"intent": "repeat_purchase_intent", "example_text": "do you have more for me?", "example_id": "repeat_purchase_intent_05", "hard_negative_group": "content_curiosity", "source": "phase47", "notes": "repeat indirect"},

    # 10. post_purchase
    {"intent": "post_purchase", "example_text": "I just bought it!", "example_id": "post_purchase_01", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "post"},
    {"intent": "post_purchase", "example_text": "payment went through", "example_id": "post_purchase_02", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "post payment"},
    {"intent": "post_purchase", "example_text": "just paid, where is it?", "example_id": "post_purchase_03", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "post question"},
    {"intent": "post_purchase", "example_text": "got it, thanks!", "example_id": "post_purchase_04", "hard_negative_group": "appreciation", "source": "phase47", "notes": "post thanks"},
    {"intent": "post_purchase", "example_text": "I purchased the VIP bundle", "example_id": "post_purchase_05", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "post explicit"},

    # 11. aftercare
    {"intent": "aftercare", "example_text": "did you get my payment?", "example_id": "aftercare_01", "hard_negative_group": "post_purchase", "source": "phase47", "notes": "aftercare"},
    {"intent": "aftercare", "example_text": "I didn't receive the content", "example_id": "aftercare_02", "hard_negative_group": "complaint", "source": "phase47", "notes": "aftercare complaint"},
    {"intent": "aftercare", "example_text": "where is my link?", "example_id": "aftercare_03", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "aftercare link"},
    {"intent": "aftercare", "example_text": "thanks for the follow-up!", "example_id": "aftercare_04", "hard_negative_group": "appreciation", "source": "phase47", "notes": "aftercare thanks"},
    {"intent": "aftercare", "example_text": "can't access the video", "example_id": "aftercare_05", "hard_negative_group": "complaint", "source": "phase47", "notes": "aftercare access"},

    # 12. tip_interest
    {"intent": "tip_interest", "example_text": "can I tip you?", "example_id": "tip_interest_01", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "tip"},
    {"intent": "tip_interest", "example_text": "how do I send a tip?", "example_id": "tip_interest_02", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "tip question"},
    {"intent": "tip_interest", "example_text": "you deserve a tip", "example_id": "tip_interest_03", "hard_negative_group": "appreciation", "source": "phase47", "notes": "tip appreciation"},
    {"intent": "tip_interest", "example_text": "let me tip you $10", "example_id": "tip_interest_04", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "tip price"},
    {"intent": "tip_interest", "example_text": "I want to support you", "example_id": "tip_interest_05", "hard_negative_group": "purchase_intent", "source": "phase47", "notes": "tip support"},

    # 13. complaint
    {"intent": "complaint", "example_text": "this is not what I paid for", "example_id": "complaint_01", "hard_negative_group": "aftercare", "source": "phase47", "notes": "complaint"},
    {"intent": "complaint", "example_text": "I'm disappointed", "example_id": "complaint_02", "hard_negative_group": "hesitation", "source": "phase47", "notes": "complaint mild"},
    {"intent": "complaint", "example_text": "you never respond", "example_id": "complaint_03", "hard_negative_group": "hesitation", "source": "phase47", "notes": "complaint response"},
    {"intent": "complaint", "example_text": "this is a scam", "example_id": "complaint_04", "hard_negative_group": "rejection", "source": "phase47", "notes": "complaint strong"},
    {"intent": "complaint", "example_text": "I want a refund", "example_id": "complaint_05", "hard_negative_group": "negotiation", "source": "phase47", "notes": "refund"},

    # 14. custom_request
    {"intent": "custom_request", "example_text": "can you make a custom video for me?", "example_id": "custom_request_01", "hard_negative_group": "content_request", "source": "phase47", "notes": "custom"},
    {"intent": "custom_request", "example_text": "do you do customs?", "example_id": "custom_request_02", "hard_negative_group": "content_request", "source": "phase47", "notes": "custom question"},
    {"intent": "custom_request", "example_text": "I have a specific idea", "example_id": "custom_request_03", "hard_negative_group": "content_request", "source": "phase47", "notes": "custom idea"},
    {"intent": "custom_request", "example_text": "can you do a custom with my name?", "example_id": "custom_request_04", "hard_negative_group": "content_request", "source": "phase47", "notes": "custom name"},
    {"intent": "custom_request", "example_text": "how much for custom content?", "example_id": "custom_request_05", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "custom price"},

    # 15. negotiation
    {"intent": "negotiation", "example_text": "can you do $10 instead of $20?", "example_id": "negotiation_01", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "negotiation price"},
    {"intent": "negotiation", "example_text": "any discount?", "example_id": "negotiation_02", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "discount"},
    {"intent": "negotiation", "example_text": "can you lower the price?", "example_id": "negotiation_03", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "negotiation"},
    {"intent": "negotiation", "example_text": "what if I pay half now?", "example_id": "negotiation_04", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "negotiation half"},
    {"intent": "negotiation", "example_text": "is the price negotiable?", "example_id": "negotiation_05", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "negotiation question"},

    # 16. hesitation
    {"intent": "hesitation", "example_text": "I'm not sure", "example_id": "hesitation_01", "hard_negative_group": "rejection", "source": "phase47", "notes": "hesitation"},
    {"intent": "hesitation", "example_text": "maybe later", "example_id": "hesitation_02", "hard_negative_group": "rejection", "source": "phase47", "notes": "hesitation maybe"},
    {"intent": "hesitation", "example_text": "let me think about it", "example_id": "hesitation_03", "hard_negative_group": "rejection", "source": "phase47", "notes": "hesitation think"},
    {"intent": "hesitation", "example_text": "I need to think", "example_id": "hesitation_04", "hard_negative_group": "rejection", "source": "phase47", "notes": "hesitation think"},
    {"intent": "hesitation", "example_text": "not sure if I can afford it", "example_id": "hesitation_05", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "hesitation afford"},

    # 17. rejection
    {"intent": "rejection", "example_text": "no thanks", "example_id": "rejection_01", "hard_negative_group": "hesitation", "source": "phase47", "notes": "rejection"},
    {"intent": "rejection", "example_text": "I don't want it", "example_id": "rejection_02", "hard_negative_group": "hesitation", "source": "phase47", "notes": "rejection"},
    {"intent": "rejection", "example_text": "not interested", "example_id": "rejection_03", "hard_negative_group": "hesitation", "source": "phase47", "notes": "rejection"},
    {"intent": "rejection", "example_text": "please stop", "example_id": "rejection_04", "hard_negative_group": "complaint", "source": "phase47", "notes": "rejection stop"},
    {"intent": "rejection", "example_text": "leave me alone", "example_id": "rejection_05", "hard_negative_group": "complaint", "source": "phase47", "notes": "rejection strong"},

    # 18. uncertain
    {"intent": "uncertain", "example_text": "idk", "example_id": "uncertain_01", "hard_negative_group": None, "source": "phase47", "notes": "uncertain short"},
    {"intent": "uncertain", "example_text": "maybe", "example_id": "uncertain_02", "hard_negative_group": "hesitation", "source": "phase47", "notes": "uncertain maybe"},
    {"intent": "uncertain", "example_text": "hmm", "example_id": "uncertain_03", "hard_negative_group": None, "source": "phase47", "notes": "uncertain hmm"},
    {"intent": "uncertain", "example_text": "not sure what you mean", "example_id": "uncertain_04", "hard_negative_group": "hesitation", "source": "phase47", "notes": "uncertain not sure"},
    {"intent": "uncertain", "example_text": "k", "example_id": "uncertain_05", "hard_negative_group": None, "source": "phase47", "notes": "uncertain k"},

    # 19. reassurance
    {"intent": "reassurance", "example_text": "is it safe to pay?", "example_id": "reassurance_01", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "reassurance safe"},
    {"intent": "reassurance", "example_text": "will you keep it private?", "example_id": "reassurance_02", "hard_negative_group": "price_inquiry", "source": "phase47", "notes": "reassurance private"},
    {"intent": "reassurance", "example_text": "can I trust you?", "example_id": "reassurance_03", "hard_negative_group": "complaint", "source": "phase47", "notes": "reassurance trust"},
    {"intent": "reassurance", "example_text": "is this legit?", "example_id": "reassurance_04", "hard_negative_group": "complaint", "source": "phase47", "notes": "reassurance legit"},
    {"intent": "reassurance", "example_text": "are you real?", "example_id": "reassurance_05", "hard_negative_group": "operator_request", "source": "phase47", "notes": "reassurance real"},

    # 20. appreciation
    {"intent": "appreciation", "example_text": "thank you so much!", "example_id": "appreciation_01", "hard_negative_group": "relationship_building", "source": "phase47", "notes": "thanks"},
    {"intent": "appreciation", "example_text": "you're amazing", "example_id": "appreciation_02", "hard_negative_group": "relationship_building", "source": "phase47", "notes": "amazing"},
    {"intent": "appreciation", "example_text": "I really appreciate you", "example_id": "appreciation_03", "hard_negative_group": "relationship_building", "source": "phase47", "notes": "appreciation"},
    {"intent": "appreciation", "example_text": "thanks babe", "example_id": "appreciation_04", "hard_negative_group": "relationship_building", "source": "phase47", "notes": "thanks slang"},
    {"intent": "appreciation", "example_text": "you made my day", "example_id": "appreciation_05", "hard_negative_group": "relationship_building", "source": "phase47", "notes": "appreciation day"},

    # 21. operator_request
    {"intent": "operator_request", "example_text": "can I talk to a human?", "example_id": "operator_request_01", "hard_negative_group": "reassurance", "source": "phase47", "notes": "human"},
    {"intent": "operator_request", "example_text": "I need help from support", "example_id": "operator_request_02", "hard_negative_group": "complaint", "source": "phase47", "notes": "support"},
    {"intent": "operator_request", "example_text": "connect me to someone", "example_id": "operator_request_03", "hard_negative_group": "reassurance", "source": "phase47", "notes": "connect"},
    {"intent": "operator_request", "example_text": "are you a bot?", "example_id": "operator_request_04", "hard_negative_group": "reassurance", "source": "phase47", "notes": "bot"},
    {"intent": "operator_request", "example_text": "I want to talk to a real person", "example_id": "operator_request_05", "hard_negative_group": "reassurance", "source": "phase47", "notes": "real person"},

    # 22. other (21st? Actually other is extra, but we need 21 total, we have 21 now incl other)
    {"intent": "other", "example_text": "what time is it?", "example_id": "other_01", "hard_negative_group": None, "source": "phase47", "notes": "other time"},
    {"intent": "other", "example_text": "do you like pizza?", "example_id": "other_02", "hard_negative_group": None, "source": "phase47", "notes": "other pizza"},
    {"intent": "other", "example_text": "my cat is cute", "example_id": "other_03", "hard_negative_group": None, "source": "phase47", "notes": "other cat"},
    {"intent": "other", "example_text": "it's raining today", "example_id": "other_04", "hard_negative_group": None, "source": "phase47", "notes": "other weather"},
    {"intent": "other", "example_text": "I have to go now", "example_id": "other_05", "hard_negative_group": None, "source": "phase47", "notes": "other go"},
]

# Validation helper
def validate_corpus() -> tuple[bool, str]:
    from collections import Counter
    intents = [e["intent"] for e in INTENT_CORPUS]
    cnt = Counter(intents)
    if len(INTENT_CORPUS) != 110:
        return False, f"expected 110, got {len(INTENT_CORPUS)}"
    for intent, c in cnt.items():
        if c != 5:
            return False, f"intent {intent} has {c} not 5"
    # No duplicate text
    texts = [e["example_text"].lower().strip() for e in INTENT_CORPUS]
    if len(texts) != len(set(texts)):
        return False, "duplicate example_text"
    return True, "ok"

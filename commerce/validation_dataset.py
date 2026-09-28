"""Validation Dataset — Phase 50
Independent from intent_corpus.py (110 reference examples).

This is 110 validation messages: 22 intents ×5, different linguistic
realizations, not copied, not trivial paraphrases.

Used for offline evaluation of UnifiedSignals vs CommerceSignals,
purchase safety, hard negatives, decision equivalence.

Do NOT use this as training data. Do NOT merge with reference corpus.
"""
from __future__ import annotations

from typing import TypedDict

class ValidationExample(TypedDict):
    text: str
    intent: str
    notes: str

# 22 ×5 =110 — independent, challenging
VALIDATION_DATASET: list[ValidationExample] = [
    # greeting (5) — hard negative vs casual_chat
    {"text": "hey there", "intent": "greeting", "notes": "short greeting, not casual"},
    {"text": "good morning!", "intent": "greeting", "notes": "greeting with punctuation"},
    {"text": "yo", "intent": "greeting", "notes": "slang greeting"},
    {"text": "hello, are you there?", "intent": "greeting", "notes": "greeting question"},
    {"text": "heya", "intent": "greeting", "notes": "typo greeting"},

    # casual_chat
    {"text": "haha that's great", "intent": "casual_chat", "notes": "casual laugh"},
    {"text": "yeah I get you", "intent": "casual_chat", "notes": "casual agree"},
    {"text": "lol", "intent": "casual_chat", "notes": "short casual"},
    {"text": "same here haha", "intent": "casual_chat", "notes": "casual same"},
    {"text": "just vibing tonight", "intent": "casual_chat", "notes": "casual vibing"},

    # relationship_building
    {"text": "I really enjoy chatting with you", "intent": "relationship_building", "notes": "relationship"},
    {"text": "you always make me feel better", "intent": "relationship_building", "notes": "relationship comfort"},
    {"text": "you're so kind", "intent": "relationship_building", "notes": "kind"},
    {"text": "I feel close to you", "intent": "relationship_building", "notes": "close"},
    {"text": "talking to you is my favorite part of the day", "intent": "relationship_building", "notes": "long relationship"},

    # personal_disclosure
    {"text": "I just moved to Austin", "intent": "personal_disclosure", "notes": "personal move, not Chicago"},
    {"text": "my cat Milo keeps me up", "intent": "personal_disclosure", "notes": "pet Milo, not Max"},
    {"text": "I'm 28 and work as a teacher", "intent": "personal_disclosure", "notes": "age/occupation"},
    {"text": "I have a brother who lives nearby", "intent": "personal_disclosure", "notes": "family"},
    {"text": "I go to the gym every morning", "intent": "personal_disclosure", "notes": "routine"},

    # content_curiosity
    {"text": "what sort of stuff do you share?", "intent": "content_curiosity", "notes": "curiosity not request"},
    {"text": "are your photos exclusive?", "intent": "content_curiosity", "notes": "curiosity exclusive"},
    {"text": "I've heard you make amazing content", "intent": "content_curiosity", "notes": "curiosity hearsay"},
    {"text": "tell me about your content style", "intent": "content_curiosity", "notes": "curiosity style"},
    {"text": "do you post often?", "intent": "content_curiosity", "notes": "curiosity often"},

    # content_request
    {"text": "please send me a picture", "intent": "content_request", "notes": "explicit request"},
    {"text": "could you share a vid?", "intent": "content_request", "notes": "request vid typo"},
    {"text": "I wanna see you", "intent": "content_request", "notes": "slang request"},
    {"text": "show me something", "intent": "content_request", "notes": "indirect request"},
    {"text": "can I have a photo?", "intent": "content_request", "notes": "request question"},

    # price_inquiry
    {"text": "what does it cost?", "intent": "price_inquiry", "notes": "price cost"},
    {"text": "is there a fee?", "intent": "price_inquiry", "notes": "fee vs price"},
    {"text": "how much for a single pic?", "intent": "price_inquiry", "notes": "price single"},
    {"text": "do you charge?", "intent": "price_inquiry", "notes": "charge"},
    {"text": "what are your rates?", "intent": "price_inquiry", "notes": "rates"},

    # purchase_intent (hard negative vs content_curiosity)
    {"text": "I wanna buy now", "intent": "purchase_intent", "notes": "purchase now"},
    {"text": "I'm ready to pay", "intent": "purchase_intent", "notes": "ready pay, not how to pay?"},
    {"text": "let's do it, take my payment", "intent": "purchase_intent", "notes": "take payment"},
    {"text": "I want to purchase", "intent": "purchase_intent", "notes": "purchase formal"},
    {"text": "sign me up", "intent": "purchase_intent", "notes": "sign up indirect purchase"},

    # repeat_purchase_intent
    {"text": "I want to buy again, loved last time", "intent": "repeat_purchase_intent", "notes": "repeat love"},
    {"text": "can I get another bundle?", "intent": "repeat_purchase_intent", "notes": "repeat bundle"},
    {"text": "more please, that was great", "intent": "repeat_purchase_intent", "notes": "repeat more"},
    {"text": "I need a second custom", "intent": "repeat_purchase_intent", "notes": "repeat custom"},
    {"text": "got anything else for me?", "intent": "repeat_purchase_intent", "notes": "repeat else"},

    # post_purchase
    {"text": "I just paid for it", "intent": "post_purchase", "notes": "post paid"},
    {"text": "just completed the purchase", "intent": "post_purchase", "notes": "post completed"},
    {"text": "my payment succeeded", "intent": "post_purchase", "notes": "post succeeded"},
    {"text": "I bought your bundle!", "intent": "post_purchase", "notes": "post bought"},
    {"text": "transaction done", "intent": "post_purchase", "notes": "post transaction"},

    # aftercare
    {"text": "I can't find the link you sent", "intent": "aftercare", "notes": "aftercare link"},
    {"text": "the video won't play", "intent": "aftercare", "notes": "aftercare play"},
    {"text": "where did my purchase go?", "intent": "aftercare", "notes": "aftercare where"},
    {"text": "I need help accessing", "intent": "aftercare", "notes": "aftercare help"},
    {"text": "link expired?", "intent": "aftercare", "notes": "aftercare expired"},

    # tip_interest
    {"text": "can I send you a tip?", "intent": "tip_interest", "notes": "tip"},
    {"text": "I want to tip you extra", "intent": "tip_interest", "notes": "tip extra"},
    {"text": "how to tip?", "intent": "tip_interest", "notes": "tip how"},
    {"text": "you deserve $5", "intent": "tip_interest", "notes": "tip deserve"},
    {"text": "let me support you with a tip", "intent": "tip_interest", "notes": "tip support"},

    # complaint
    {"text": "this wasn't worth it", "intent": "complaint", "notes": "complaint worth"},
    {"text": "I'm unhappy with the content", "intent": "complaint", "notes": "complaint unhappy"},
    {"text": "you took too long to reply", "intent": "complaint", "notes": "complaint reply"},
    {"text": "this feels like a scam", "intent": "complaint", "notes": "complaint scam"},
    {"text": "I expected more", "intent": "complaint", "notes": "complaint expected"},

    # custom_request
    {"text": "could you make something just for me?", "intent": "custom_request", "notes": "custom just for me"},
    {"text": "do you do personalized videos?", "intent": "custom_request", "notes": "custom personalized"},
    {"text": "can you say my name in a video?", "intent": "custom_request", "notes": "custom name"},
    {"text": "I have a special request", "intent": "custom_request", "notes": "custom special"},
    {"text": "custom content possible?", "intent": "custom_request", "notes": "custom possible"},

    # negotiation
    {"text": "would you do $5 instead?", "intent": "negotiation", "notes": "negotiation $5"},
    {"text": "any way to get a deal?", "intent": "negotiation", "notes": "negotiation deal"},
    {"text": "can we negotiate price?", "intent": "negotiation", "notes": "negotiation negotiate"},
    {"text": "too expensive, can you lower?", "intent": "negotiation", "notes": "negotiation lower"},
    {"text": "is $10 okay?", "intent": "negotiation", "notes": "negotiation $10"},

    # hesitation
    {"text": "I'm thinking about it", "intent": "hesitation", "notes": "hesitation thinking"},
    {"text": "maybe tomorrow", "intent": "hesitation", "notes": "hesitation tomorrow"},
    {"text": "not sure yet", "intent": "hesitation", "notes": "hesitation not sure"},
    {"text": "let me consider", "intent": "hesitation", "notes": "hesitation consider"},
    {"text": "I might, not certain", "intent": "hesitation", "notes": "hesitation might"},

    # rejection
    {"text": "no, not interested", "intent": "rejection", "notes": "rejection not interested"},
    {"text": "I'll pass", "intent": "rejection", "notes": "rejection pass"},
    {"text": "don't want it", "intent": "rejection", "notes": "rejection don't want"},
    {"text": "no thank you", "intent": "rejection", "notes": "rejection thank"},
    {"text": "I'm good, thanks", "intent": "rejection", "notes": "rejection good"},

    # uncertain
    {"text": "idk what to do", "intent": "uncertain", "notes": "uncertain idk"},
    {"text": "hmmm", "intent": "uncertain", "notes": "uncertain hmmm"},
    {"text": "maybe idk", "intent": "uncertain", "notes": "uncertain maybe idk"},
    {"text": "not sure what u mean", "intent": "uncertain", "notes": "uncertain not sure variant"},
    {"text": "kk", "intent": "uncertain", "notes": "uncertain kk short"},

    # reassurance
    {"text": "is this private?", "intent": "reassurance", "notes": "reassurance private"},
    {"text": "will anyone see this?", "intent": "reassurance", "notes": "reassurance see"},
    {"text": "is it secure?", "intent": "reassurance", "notes": "reassurance secure"},
    {"text": "do you keep things confidential?", "intent": "reassurance", "notes": "reassurance confidential"},
    {"text": "can I trust this?", "intent": "reassurance", "notes": "reassurance trust"},

    # appreciation
    {"text": "thanks a lot!", "intent": "appreciation", "notes": "appreciation thanks"},
    {"text": "you're the best", "intent": "appreciation", "notes": "appreciation best"},
    {"text": "appreciate you", "intent": "appreciation", "notes": "appreciation"},
    {"text": "thank you babe", "intent": "appreciation", "notes": "appreciation babe"},
    {"text": "so grateful", "intent": "appreciation", "notes": "grateful"},

    # operator_request
    {"text": "I need to speak to support", "intent": "operator_request", "notes": "operator support"},
    {"text": "are you human?", "intent": "operator_request", "notes": "operator human"},
    {"text": "can a person help me?", "intent": "operator_request", "notes": "operator person"},
    {"text": "connect me to staff", "intent": "operator_request", "notes": "operator staff"},
    {"text": "I want a real person", "intent": "operator_request", "notes": "operator real"},

    # other
    {"text": "what's the weather?", "intent": "other", "notes": "other weather"},
    {"text": "do you like music?", "intent": "other", "notes": "other music"},
    {"text": "my dog is sleeping", "intent": "other", "notes": "other dog sleeping"},
    {"text": "it is sunny today", "intent": "other", "notes": "other sunny"},
    {"text": "I have an exam tomorrow", "intent": "other", "notes": "other exam"},
]

def validate() -> tuple[bool, str]:
    from collections import Counter
    intents = [e["intent"] for e in VALIDATION_DATASET]
    cnt = Counter(intents)
    if len(VALIDATION_DATASET) != 110:
        return False, f"expected 110, got {len(VALIDATION_DATASET)}"
    for intent, c in cnt.items():
        if c != 5:
            return False, f"{intent} has {c} not 5"
    # No overlap with intent_corpus texts
    try:
        from commerce.intent_corpus import INTENT_CORPUS
        ref_texts = set(e["example_text"].lower().strip() for e in INTENT_CORPUS)
        val_texts = [e["text"].lower().strip() for e in VALIDATION_DATASET]
        overlap = set(val_texts) & ref_texts
        if overlap:
            return False, f"overlap with corpus: {overlap}"
    except Exception:
        pass
    # No duplicate within validation
    texts = [e["text"].lower().strip() for e in VALIDATION_DATASET]
    if len(texts) != len(set(texts)):
        return False, "duplicate within validation"
    return True, "ok"

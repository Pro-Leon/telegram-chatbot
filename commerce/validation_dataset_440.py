"""440-Case Independent Validation Dataset — Phase 66

22 intents × 20 cases = 440 total.
Independent from intent_corpus.py (110 reference examples) and
from validation_dataset.py (110-case validation set).

Designed for:
- Cross-architecture evaluation (offline vs online)
- Pre-deployment offline baseline measurement
- Runtime monitoring (sampling + regression detection)
- Long-term migration benchmark

Usage:
    from commerce.validation_dataset_440 import VALIDATION_440, validate_440
    ok, msg = validate_440()

Fields:
    text          — message text
    intent        — ground-truth intent label
    notes         — brief description of test purpose
    example_id    — unique identifier (int 1–440)
    hard_negative_for — intent this example is designed to confuse (may be None)
    context       — stateful context (conversation_history, user_state, or None)
    length_category — short / medium / long
    style         — linguistic style (slang, formal, emoji, fragmented, etc.)

Do NOT use as training data. Do NOT merge with reference corpus.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class ValidationExample440(TypedDict):
    text: str
    intent: str
    notes: str
    example_id: int
    hard_negative_for: Optional[str]
    context: Optional[dict]
    length_category: str
    style: str


VALIDATION_440: list[ValidationExample440] = [
    # ──────────────────────────────────────────────────────────────
    # GREETING (1–20) — hard negative vs casual_chat
    # ──────────────────────────────────────────────────────────────
    {"text": "heyyy you", "intent": "greeting", "notes": "enthusiastic greeting", "example_id": 1, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "hi, how are you doing?", "intent": "greeting", "notes": "greeting with follow-up question", "example_id": 2, "hard_negative_for": None, "context": None, "length_category": "short", "style": "neutral"},
    {"text": "good evening!", "intent": "greeting", "notes": "time-of-day greeting", "example_id": 3, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "formal"},
    {"text": "hellooo", "intent": "greeting", "notes": "extended greeting", "example_id": 4, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "hey, long time no see", "intent": "greeting", "notes": "reunion greeting", "example_id": 5, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "hi there, it's been a while", "intent": "greeting", "notes": "reconnection greeting", "example_id": 6, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "sup", "intent": "greeting", "notes": "minimal slang greeting", "example_id": 7, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "slang"},
    {"text": "good morning beautiful", "intent": "greeting", "notes": "compliment-greeting", "example_id": 8, "hard_negative_for": "relationship_building", "context": None, "length_category": "short", "style": "casual"},
    {"text": "hi! been thinking about you", "intent": "greeting", "notes": "greeting + emotion", "example_id": 9, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "heya, what's up?", "intent": "greeting", "notes": "casual greeting question", "example_id": 10, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "slang"},
    {"text": "hey, just checking in on you", "intent": "greeting", "notes": "checking-in greeting", "example_id": 11, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "yo, you around?", "intent": "greeting", "notes": "availability-seeking greeting", "example_id": 12, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "slang"},
    {"text": "hi sweetie", "intent": "greeting", "notes": "pet-name greeting", "example_id": 13, "hard_negative_for": "relationship_building", "context": None, "length_category": "short", "style": "casual"},
    {"text": "hello, hope you're well", "intent": "greeting", "notes": "formal polite greeting", "example_id": 14, "hard_negative_for": None, "context": None, "length_category": "short", "style": "formal"},
    {"text": "hey gorgeous", "intent": "greeting", "notes": "compliment greeting", "example_id": 15, "hard_negative_for": "relationship_building", "context": None, "length_category": "short", "style": "casual"},
    {"text": "what's good", "intent": "greeting", "notes": "urban greeting", "example_id": 16, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "slang"},
    {"text": "hi, nice to meet you", "intent": "greeting", "notes": "first-meeting greeting", "example_id": 17, "hard_negative_for": None, "context": None, "length_category": "short", "style": "formal"},
    {"text": "hey there stranger", "intent": "greeting", "notes": "playful reunion", "example_id": 18, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "hey, what's new with you?", "intent": "greeting", "notes": "greeting with curiosity", "example_id": 19, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "yo, it's me again", "intent": "greeting", "notes": "returning user greeting", "example_id": 20, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "slang"},

    # ──────────────────────────────────────────────────────────────
    # CASUAL_CHAT (21–40) — hard negative vs greeting, relationship_building
    # ──────────────────────────────────────────────────────────────
    {"text": "that's so funny lol", "intent": "casual_chat", "notes": "laughter reaction", "example_id": 21, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "omg no way!", "intent": "casual_chat", "notes": "surprise reaction", "example_id": 22, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "haha yeah exactly", "intent": "casual_chat", "notes": "agreement", "example_id": 23, "hard_negative_for": None, "context": None, "length_category": "short", "style": "neutral"},
    {"text": "that reminds me of something that happened to me yesterday", "intent": "casual_chat", "notes": "storytelling opener", "example_id": 24, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "same lol", "intent": "casual_chat", "notes": "minimal agreement", "example_id": 25, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "oh wow that's crazy", "intent": "casual_chat", "notes": "reaction", "example_id": 26, "hard_negative_for": None, "context": None, "length_category": "short", "style": "casual"},
    {"text": "hahaha you're so right about that", "intent": "casual_chat", "notes": "agreement with humor", "example_id": 27, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "nooo way that actually happened??", "intent": "casual_chat", "notes": "disbelief reaction", "example_id": 28, "hard_negative_for": None, "context": None, "length_category": "medium", "style": "slang"},
    {"text": "lmao you're hilarious", "intent": "casual_chat", "notes": "humor appreciation", "example_id": 29, "hard_negative_for": "appreciation", "context": None, "length_category": "short", "style": "slang"},
    {"text": "yeah for real though", "intent": "casual_chat", "notes": "casual agreement", "example_id": 30, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "omg stop it you", "intent": "casual_chat", "notes": "playful reaction", "example_id": 31, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "that's wild, tell me more", "intent": "casual_chat", "notes": "curiosity in casual chat", "example_id": 32, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "casual"},
    {"text": "haha I can't believe you just said that", "intent": "casual_chat", "notes": "surprise reaction", "example_id": 33, "hard_negative_for": None, "context": None, "length_category": "medium", "style": "casual"},
    {"text": "ngl that's pretty cool", "intent": "casual_chat", "notes": "casual approval", "example_id": 34, "hard_negative_for": "appreciation", "context": None, "length_category": "short", "style": "slang"},
    {"text": "bruh", "intent": "casual_chat", "notes": "minimal reaction", "example_id": 35, "hard_negative_for": None, "context": None, "length_category": "short", "style": "slang"},
    {"text": "ooh that sounds fun", "intent": "casual_chat", "notes": "casual reaction", "example_id": 36, "hard_negative_for": None, "context": None, "length_category": "short", "style": "casual"},
    {"text": "wait really? that's amazing", "intent": "casual_chat", "notes": "surprise + reaction", "example_id": 37, "hard_negative_for": None, "context": None, "length_category": "medium", "style": "casual"},
    {"text": "lol ok fine you win this one", "intent": "casual_chat", "notes": "playful concession", "example_id": 38, "hard_negative_for": None, "context": None, "length_category": "medium", "style": "casual"},
    {"text": "haha you always crack me up", "intent": "casual_chat", "notes": "humor + warmth", "example_id": 39, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "that's fair I guess", "intent": "casual_chat", "notes": "mild agreement", "example_id": 40, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # RELATIONSHIP_BUILDING (41–60) — hard negative vs casual_chat, greeting
    # ──────────────────────────────────────────────────────────────
    {"text": "you always know how to make me smile", "intent": "relationship_building", "notes": "emotional warmth", "example_id": 41, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I feel like we really connect", "intent": "relationship_building", "notes": "connection statement", "example_id": 42, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you mean so much to me", "intent": "relationship_building", "notes": "emotional declaration", "example_id": 43, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I've never felt this way about anyone", "intent": "relationship_building", "notes": "deep emotion", "example_id": 44, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you're my favorite person to talk to", "intent": "relationship_building", "notes": "preference declaration", "example_id": 45, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "honestly you're the best thing that's happened to me in a while", "intent": "relationship_building", "notes": "deep appreciation", "example_id": 46, "hard_negative_for": "appreciation", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "I look forward to chatting with you every day", "intent": "relationship_building", "notes": "routine + warmth", "example_id": 47, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you really get me, you know that?", "intent": "relationship_building", "notes": "understanding", "example_id": 48, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I feel safe talking to you about anything", "intent": "relationship_building", "notes": "trust + safety", "example_id": 49, "hard_negative_for": "reassurance", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you're unlike anyone I've ever met", "intent": "relationship_building", "notes": "uniqueness declaration", "example_id": 50, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "thinking of you right now", "intent": "relationship_building", "notes": "affectionate thought", "example_id": 51, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "you make my day so much better", "intent": "relationship_building", "notes": "positive impact", "example_id": 52, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I really care about you", "intent": "relationship_building", "notes": "care declaration", "example_id": 53, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "honestly you're everything to me", "intent": "relationship_building", "notes": "deep declaration", "example_id": 54, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I can't imagine not having you in my life", "intent": "relationship_building", "notes": "attachment", "example_id": 55, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you always brighten my day", "intent": "relationship_building", "notes": "daily warmth", "example_id": 56, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "talking to you is the highlight of my day", "intent": "relationship_building", "notes": "importance declaration", "example_id": 57, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you're so special to me", "intent": "relationship_building", "notes": "specialness", "example_id": 58, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I feel like we have something really special", "intent": "relationship_building", "notes": "shared bond", "example_id": 59, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "you mean the world to me", "intent": "relationship_building", "notes": "world-level importance", "example_id": 60, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # PERSONAL_DISCLOSURE (61–80) — hard negative vs casual_chat, other
    # ──────────────────────────────────────────────────────────────
    {"text": "I just adopted a puppy yesterday, his name is Cooper", "intent": "personal_disclosure", "notes": "pet adoption", "example_id": 61, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm a nurse and I work night shifts", "intent": "personal_disclosure", "notes": "occupation detail", "example_id": 62, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I just finished a 12 hour shift, exhausted", "intent": "personal_disclosure", "notes": "work context + feeling", "example_id": 63, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I live in Seattle and I love the rain", "intent": "personal_disclosure", "notes": "location + preference", "example_id": 64, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm training for a marathon next month", "intent": "personal_disclosure", "notes": "fitness goal", "example_id": 65, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "my favorite food is sushi, could eat it every day", "intent": "personal_disclosure", "notes": "preference", "example_id": 66, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I just moved here from Chicago last week", "intent": "personal_disclosure", "notes": "relocation", "example_id": 67, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I have a twin sister, we're really close", "intent": "personal_disclosure", "notes": "family detail", "example_id": 68, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I work as a software engineer at a startup", "intent": "personal_disclosure", "notes": "occupation detail", "example_id": 69, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm going through a breakup right now", "intent": "personal_disclosure", "notes": "relationship status", "example_id": 70, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I have two cats and they fight over the couch", "intent": "personal_disclosure", "notes": "pet life", "example_id": 71, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I just started painting as a hobby", "intent": "personal_disclosure", "notes": "new hobby", "example_id": 72, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "my mom's birthday is coming up, need gift ideas", "intent": "personal_disclosure", "notes": "family event", "example_id": 73, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm 25 and still figuring things out", "intent": "personal_disclosure", "notes": "age + introspection", "example_id": 74, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I got promoted last week!", "intent": "personal_disclosure", "notes": "career milestone", "example_id": 75, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm vegetarian, been one for 3 years now", "intent": "personal_disclosure", "notes": "dietary choice", "example_id": 76, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm thinking about getting a tattoo", "intent": "personal_disclosure", "notes": "consideration", "example_id": 77, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I grew up in a small town in Ohio", "intent": "personal_disclosure", "notes": "origin story", "example_id": 78, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I just started therapy and it's really helping", "intent": "personal_disclosure", "notes": "mental health", "example_id": 79, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I have a fear of heights, totally irrational I know", "intent": "personal_disclosure", "notes": "phobia", "example_id": 80, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # CONTENT_CURIOSITY (81–100) — hard negative vs content_request, price_inquiry
    # ──────────────────────────────────────────────────────────────
    {"text": "what kind of stuff do you usually post?", "intent": "content_curiosity", "notes": "genre curiosity", "example_id": 81, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "do you post every day or just sometimes?", "intent": "content_curiosity", "notes": "frequency curiosity", "example_id": 82, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is your content explicit or more tame?", "intent": "content_curiosity", "notes": "nature curiosity", "example_id": 83, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's the best thing you've ever created?", "intent": "content_curiosity", "notes": "quality curiosity", "example_id": 84, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "do you do themed content or just random stuff?", "intent": "content_curiosity", "notes": "style curiosity", "example_id": 85, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "how long have you been doing this?", "intent": "content_curiosity", "notes": "tenure curiosity", "example_id": 86, "hard_negative_for": "content_request", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "what inspired you to start creating content?", "intent": "content_curiosity", "notes": "motivation curiosity", "example_id": 87, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "are your photos professionally shot or amateur?", "intent": "content_curiosity", "notes": "quality curiosity", "example_id": 88, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's your most popular content type?", "intent": "content_curiosity", "notes": "popularity curiosity", "example_id": 89, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "do you ever do collaborations?", "intent": "content_curiosity", "notes": "collaboration curiosity", "example_id": 90, "hard_negative_for": "content_request", "context": None, "length_category": "short", "style": "casual"},
    {"text": "what's the difference between your free and paid stuff?", "intent": "content_curiosity", "notes": "tier curiosity (not price)", "example_id": 91, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can you tell me more about what you offer?", "intent": "content_curiosity", "notes": "offer curiosity, not request", "example_id": 92, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "how did you get into this line of work?", "intent": "content_curiosity", "notes": "origin curiosity", "example_id": 93, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what does a typical day of content creation look like for you?", "intent": "content_curiosity", "notes": "process curiosity", "example_id": 94, "hard_negative_for": "content_request", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "do you take requests or just do your own thing?", "intent": "content_curiosity", "notes": "process curiosity", "example_id": 95, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's the vibe of your page overall?", "intent": "content_curiosity", "notes": "aesthetic curiosity", "example_id": 96, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I've heard great things about your content, what makes it special?", "intent": "content_curiosity", "notes": "quality curiosity via hearsay", "example_id": 97, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "do you post more photos or videos?", "intent": "content_curiosity", "notes": "format curiosity", "example_id": 98, "hard_negative_for": "content_request", "context": None, "length_category": "short", "style": "casual"},
    {"text": "what's your content creation process like?", "intent": "content_curiosity", "notes": "process curiosity", "example_id": 99, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "how often do you update your page?", "intent": "content_curiosity", "notes": "update frequency curiosity", "example_id": 100, "hard_negative_for": "content_request", "context": None, "length_category": "short", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # CONTENT_REQUEST (101–120) — hard negative vs content_curiosity, price_inquiry
    # ──────────────────────────────────────────────────────────────
    {"text": "send me something hot", "intent": "content_request", "notes": "explicit request", "example_id": 101, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "slang"},
    {"text": "can you send a video of you dancing?", "intent": "content_request", "notes": "specific request", "example_id": 102, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want to see what you look like right now", "intent": "content_request", "notes": "real-time request", "example_id": 103, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "drop a pic", "intent": "content_request", "notes": "minimal request", "example_id": 104, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "slang"},
    {"text": "would you send me something exclusive?", "intent": "content_request", "notes": "exclusive request", "example_id": 105, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'd love to see more of you", "intent": "content_request", "notes": "indirect request", "example_id": 106, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "could you send a selfie?", "intent": "content_request", "notes": "selfie request", "example_id": 107, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "I wanna see your outfit today", "intent": "content_request", "notes": "specific content request", "example_id": 108, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "send me what you've been working on", "intent": "content_request", "notes": "work reveal request", "example_id": 109, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can I get a sneak peek?", "intent": "content_request", "notes": "preview request", "example_id": 110, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "casual"},
    {"text": "show me something new", "intent": "content_request", "notes": "new content request", "example_id": 111, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I need to see you right now", "intent": "content_request", "notes": "urgent request", "example_id": 112, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can you share a full body shot?", "intent": "content_request", "notes": "specific type request", "example_id": 113, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'd pay to see that", "intent": "content_request", "notes": "conditional request with payment", "example_id": 114, "hard_negative_for": "price_inquiry", "context": None, "length_category": "short", "style": "casual"},
    {"text": "send something just for me", "intent": "content_request", "notes": "personalized request", "example_id": 115, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "how about a video?", "intent": "content_request", "notes": "format request", "example_id": 116, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "I want to see your latest content", "intent": "content_request", "notes": "recent content request", "example_id": 117, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "drop that photo you were teasing earlier", "intent": "content_request", "notes": "callback request", "example_id": 118, "hard_negative_for": "content_curiosity", "context": {"conversation_history": ["Earlier you mentioned you had something to share"]}, "length_category": "medium", "style": "casual"},
    {"text": "I need a new wallpaper, send me something good", "intent": "content_request", "notes": "use-case request", "example_id": 119, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "send me that video you posted about", "intent": "content_request", "notes": "reference request", "example_id": 120, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # PRICE_INQUIRY (121–140) — hard negative vs purchase_intent, content_request
    # ──────────────────────────────────────────────────────────────
    {"text": "what's the price for a custom video?", "intent": "price_inquiry", "notes": "custom price", "example_id": 121, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's your subscription price?", "intent": "price_inquiry", "notes": "subscription price", "example_id": 122, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "do you have any discounts right now?", "intent": "price_inquiry", "notes": "discount inquiry", "example_id": 123, "hard_negative_for": "negotiation", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what does the premium bundle include and what's the price?", "intent": "price_inquiry", "notes": "bundle details + price", "example_id": 124, "hard_negative_for": "purchase_intent", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "can you send me your rate card?", "intent": "price_inquiry", "notes": "rate card request", "example_id": 125, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "formal"},
    {"text": "is there a monthly fee or just one-time?", "intent": "price_inquiry", "notes": "billing model", "example_id": 126, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's included in the free tier?", "intent": "price_inquiry", "notes": "free tier inquiry", "example_id": 127, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "how much does it cost to unlock everything?", "intent": "price_inquiry", "notes": "full access price", "example_id": 128, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what are your prices for custom requests?", "intent": "price_inquiry", "notes": "custom pricing", "example_id": 129, "hard_negative_for": "custom_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "do you offer any bundle deals?", "intent": "price_inquiry", "notes": "bundle pricing", "example_id": 130, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "I'm comparing prices, what's yours?", "intent": "price_inquiry", "notes": "competitive pricing", "example_id": 131, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's the cheapest option you have?", "intent": "price_inquiry", "notes": "budget inquiry", "example_id": 132, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "are there any hidden fees I should know about?", "intent": "price_inquiry", "notes": "transparency inquiry", "example_id": 133, "hard_negative_for": "reassurance", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's the difference in price between tiers?", "intent": "price_inquiry", "notes": "tier pricing comparison", "example_id": 134, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can I pay with crypto?", "intent": "price_inquiry", "notes": "payment method inquiry", "example_id": 135, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "what's your cheapest bundle?", "intent": "price_inquiry", "notes": "budget bundle", "example_id": 136, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "do you charge extra for customs?", "intent": "price_inquiry", "notes": "surcharge inquiry", "example_id": 137, "hard_negative_for": "custom_request", "context": None, "length_category": "short", "style": "casual"},
    {"text": "what does $20 get me?", "intent": "price_inquiry", "notes": "value inquiry", "example_id": 138, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "how much for a 5 minute video?", "intent": "price_inquiry", "notes": "duration-based pricing", "example_id": 139, "hard_negative_for": "custom_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "what's the annual subscription rate?", "intent": "price_inquiry", "notes": "annual pricing", "example_id": 140, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # PURCHASE_INTENT (141–160) — hard negative vs price_inquiry, content_request
    # ──────────────────────────────────────────────────────────────
    {"text": "I'm ready to buy right now", "intent": "purchase_intent", "notes": "immediate purchase", "example_id": 141, "hard_negative_for": "price_inquiry", "context": None, "length_category": "short", "style": "casual"},
    {"text": "take my money, I want the full bundle", "intent": "purchase_intent", "notes": "full bundle purchase", "example_id": 142, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "slang"},
    {"text": "I'm signing up for your premium tier", "intent": "purchase_intent", "notes": "tier selection + purchase", "example_id": 143, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "how do I send you payment?", "intent": "purchase_intent", "notes": "payment logistics, not price inquiry", "example_id": 144, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "let's do this, I want to subscribe", "intent": "purchase_intent", "notes": "subscription decision", "example_id": 145, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to buy the custom video we talked about", "intent": "purchase_intent", "notes": "follow-up purchase", "example_id": 146, "hard_negative_for": "price_inquiry", "context": {"conversation_history": ["Earlier we discussed a custom video idea"]}, "length_category": "medium", "style": "casual"},
    {"text": "sign me up for the annual plan", "intent": "purchase_intent", "notes": "annual purchase", "example_id": 147, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to purchase your exclusive content pack", "intent": "purchase_intent", "notes": "specific product purchase", "example_id": 148, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "let's lock this in, I'm buying", "intent": "purchase_intent", "notes": "committed purchase", "example_id": 149, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want the bundle you offered me", "intent": "purchase_intent", "notes": "callback purchase", "example_id": 150, "hard_negative_for": "price_inquiry", "context": {"conversation_history": ["You previously offered a bundle deal"]}, "length_category": "medium", "style": "casual"},
    {"text": "add me to the premium subscriber list", "intent": "purchase_intent", "notes": "subscription request", "example_id": 151, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm buying the full collection right now", "intent": "purchase_intent", "notes": "full purchase", "example_id": 152, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "where can I send the money?", "intent": "purchase_intent", "notes": "payment logistics", "example_id": 153, "hard_negative_for": "price_inquiry", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm in, take my payment", "intent": "purchase_intent", "notes": "committed purchase", "example_id": 154, "hard_negative_for": "price_inquiry", "context": None, "length_category": "short", "style": "slang"},
    {"text": "I want to get the VIP access", "intent": "purchase_intent", "notes": "VIP purchase", "example_id": 155, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "yes, I'll take the $50 bundle", "intent": "purchase_intent", "notes": "specific bundle purchase", "example_id": 156, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want to subscribe, how do I start?", "intent": "purchase_intent", "notes": "onboarding purchase", "example_id": 157, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "let's go, I'm buying the custom video", "intent": "purchase_intent", "notes": "custom purchase", "example_id": 158, "hard_negative_for": "custom_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to purchase now, not later", "intent": "purchase_intent", "notes": "urgent purchase", "example_id": 159, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I've decided, I'm buying the premium", "intent": "purchase_intent", "notes": "decided purchase", "example_id": 160, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # REPEAT_PURCHASE_INTENT (161–180) — hard negative vs purchase_intent, appreciation
    # ──────────────────────────────────────────────────────────────
    {"text": "I want to buy another bundle, loved the last one", "intent": "repeat_purchase_intent", "notes": "repeat bundle", "example_id": 161, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous bundle purchase completed successfully"]}, "length_category": "medium", "style": "casual"},
    {"text": "can I get another custom video? yours were amazing", "intent": "repeat_purchase_intent", "notes": "repeat custom", "example_id": 162, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous custom video purchased"]}, "length_category": "medium", "style": "casual"},
    {"text": "I need more of your content, that was so good", "intent": "repeat_purchase_intent", "notes": "repeat more", "example_id": 163, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Recent purchase of content"]}, "length_category": "medium", "style": "casual"},
    {"text": "ready to buy again, what's new?", "intent": "repeat_purchase_intent", "notes": "repeat + discovery", "example_id": 164, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous purchase history"]}, "length_category": "medium", "style": "casual"},
    {"text": "I wanna re-subscribe, my last month was great", "intent": "repeat_purchase_intent", "notes": "resubscription", "example_id": 165, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous subscription expired"]}, "length_category": "medium", "style": "casual"},
    {"text": "that bundle was fire, I want another one", "intent": "repeat_purchase_intent", "notes": "repeat bundle slang", "example_id": 166, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous bundle purchased"]}, "length_category": "medium", "style": "slang"},
    {"text": "I need the sequel to what you sent me last time", "intent": "repeat_purchase_intent", "notes": "sequel request", "example_id": 167, "hard_negative_for": "custom_request", "context": {"conversation_history": ["Previous content purchased"]}, "length_category": "medium", "style": "casual"},
    {"text": "I'm coming back for more, that was incredible", "intent": "repeat_purchase_intent", "notes": "return purchase", "example_id": 168, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Positive experience with previous purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "I want to buy the upgrade, I already have the basic", "intent": "repeat_purchase_intent", "notes": "upgrade purchase", "example_id": 169, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Current basic subscription"]}, "length_category": "medium", "style": "casual"},
    {"text": "can I get the monthly bundle again? loved it", "intent": "repeat_purchase_intent", "notes": "repeat monthly", "example_id": 170, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous monthly bundle"]}, "length_category": "medium", "style": "casual"},
    {"text": "I need another one of those custom videos you did", "intent": "repeat_purchase_intent", "notes": "repeat specific custom", "example_id": 171, "hard_negative_for": "custom_request", "context": {"conversation_history": ["Previous custom video"]}, "length_category": "medium", "style": "casual"},
    {"text": "I'm ready to buy the next tier up", "intent": "repeat_purchase_intent", "notes": "tier upgrade", "example_id": 172, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Current subscription tier"]}, "length_category": "medium", "style": "casual"},
    {"text": "I want to reorder what I got last month, it was perfect", "intent": "repeat_purchase_intent", "notes": "reorder", "example_id": 173, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous month's purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "I need a second set of those photos you sent", "intent": "repeat_purchase_intent", "notes": "second set", "example_id": 174, "hard_negative_for": "content_request", "context": {"conversation_history": ["Previous photo set purchased"]}, "length_category": "medium", "style": "casual"},
    {"text": "that was so worth it, I'm buying the bundle again", "intent": "repeat_purchase_intent", "notes": "value + repeat", "example_id": 175, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous bundle"]}, "length_category": "medium", "style": "casual"},
    {"text": "I'm back, want to buy the same thing again", "intent": "repeat_purchase_intent", "notes": "same item repeat", "example_id": 176, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "I want another custom, different theme this time", "intent": "repeat_purchase_intent", "notes": "repeat custom new theme", "example_id": 177, "hard_negative_for": "custom_request", "context": {"conversation_history": ["Previous custom with different theme"]}, "length_category": "medium", "style": "casual"},
    {"text": "I want to renew my subscription before it expires", "intent": "repeat_purchase_intent", "notes": "renewal", "example_id": 178, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Current active subscription expiring soon"]}, "length_category": "medium", "style": "neutral"},
    {"text": "I need to buy the extended version this time", "intent": "repeat_purchase_intent", "notes": "version upgrade", "example_id": 179, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Previous standard version purchased"]}, "length_category": "medium", "style": "casual"},
    {"text": "coming back for round two, let's do this", "intent": "repeat_purchase_intent", "notes": "round two", "example_id": 180, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["First round completed"]}, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # POST_PURCHASE (181–200) — hard negative vs purchase_intent, aftercare
    # ──────────────────────────────────────────────────────────────
    {"text": "I just completed the purchase, thanks!", "intent": "post_purchase", "notes": "post-purchase thanks", "example_id": 181, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "payment went through, what now?", "intent": "post_purchase", "notes": "post-purchase next steps", "example_id": 182, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I just subscribed!", "intent": "post_purchase", "notes": "post-subscribe", "example_id": 183, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "transaction complete, can't wait", "intent": "post_purchase", "notes": "post-transaction", "example_id": 184, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I bought the bundle, when do I get access?", "intent": "post_purchase", "notes": "post-purchase access question", "example_id": 185, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "just sent the payment, did you get it?", "intent": "post_purchase", "notes": "payment confirmation", "example_id": 186, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I purchased the premium tier, how do I log in?", "intent": "post_purchase", "notes": "post-purchase login", "example_id": 187, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I just placed the order for the custom video", "intent": "post_purchase", "notes": "post-order custom", "example_id": 188, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "payment confirmed on my end", "intent": "post_purchase", "notes": "payment confirm", "example_id": 189, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I just bought it, super excited!", "intent": "post_purchase", "notes": "post-buy excitement", "example_id": 190, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I completed the transaction, when will I receive the content?", "intent": "post_purchase", "notes": "post-transaction delivery", "example_id": 191, "hard_negative_for": "aftercare", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "just subscribed to your page", "intent": "post_purchase", "notes": "post-subscribe action", "example_id": 192, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I sent the payment via Venmo, did it go through?", "intent": "post_purchase", "notes": "payment method confirm", "example_id": 193, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "purchase complete, looking forward to the content!", "intent": "post_purchase", "notes": "post-purchase anticipation", "example_id": 194, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I just bought the full collection", "intent": "post_purchase", "notes": "post-buy full", "example_id": 195, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "payment just went through, thanks for the deal", "intent": "post_purchase", "notes": "post-payment thanks", "example_id": 196, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I subscribed, how long until I can access everything?", "intent": "post_purchase", "notes": "post-subscribe access", "example_id": 197, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "done! purchase is complete", "intent": "post_purchase", "notes": "post-purchase done", "example_id": 198, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I just bought it, receipt says confirmed", "intent": "post_purchase", "notes": "receipt confirm", "example_id": 199, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I completed the purchase and I'm so happy", "intent": "post_purchase", "notes": "post-purchase emotion", "example_id": 200, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # AFTERCARE (201–220) — hard negative vs complaint, post_purchase
    # ──────────────────────────────────────────────────────────────
    {"text": "I bought the bundle but can't find where to download it", "intent": "aftercare", "notes": "download issue", "example_id": 201, "hard_negative_for": "complaint", "context": {"conversation_history": ["Recent bundle purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "the link you sent me is broken", "intent": "aftercare", "notes": "broken link", "example_id": 202, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I subscribed but my account still shows free tier", "intent": "aftercare", "notes": "account sync issue", "example_id": 203, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "the video won't load at all", "intent": "aftercare", "notes": "loading issue", "example_id": 204, "hard_negative_for": "complaint", "context": None, "length_category": "short", "style": "casual"},
    {"text": "where did my purchase go? I can't see it anymore", "intent": "aftercare", "notes": "missing purchase", "example_id": 205, "hard_negative_for": "complaint", "context": {"conversation_history": ["Previous purchase confirmed"]}, "length_category": "medium", "style": "casual"},
    {"text": "I'm trying to access my content but it says I don't have permission", "intent": "aftercare", "notes": "permission issue", "example_id": 206, "hard_negative_for": "complaint", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "the custom video I ordered hasn't arrived yet", "intent": "aftercare", "notes": "delivery delay", "example_id": 207, "hard_negative_for": "complaint", "context": {"conversation_history": ["Custom video ordered 2 days ago"]}, "length_category": "medium", "style": "casual"},
    {"text": "I paid but I'm not getting the content", "intent": "aftercare", "notes": "payment without delivery", "example_id": 208, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "the photos aren't downloading properly", "intent": "aftercare", "notes": "download quality", "example_id": 209, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need help with my subscription, it's not working right", "intent": "aftercare", "notes": "subscription help", "example_id": 210, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "the link expired before I could use it", "intent": "aftercare", "notes": "expired link", "example_id": 211, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I can't log in to see the content I purchased", "intent": "aftercare", "notes": "login issue", "example_id": 212, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "the video quality is blurry, is that normal?", "intent": "aftercare", "notes": "quality issue", "example_id": 213, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need technical help with accessing the premium features", "intent": "aftercare", "notes": "technical help", "example_id": 214, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "my payment went through but I haven't received anything", "intent": "aftercare", "notes": "payment without delivery", "example_id": 215, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "the content isn't showing up in my account", "intent": "aftercare", "notes": "content missing", "example_id": 216, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need help resetting my password to access my purchase", "intent": "aftercare", "notes": "password reset", "example_id": 217, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "the download link says 'access denied'", "intent": "aftercare", "notes": "access denied", "example_id": 218, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I can't find the bundle I just bought", "intent": "aftercare", "notes": "missing bundle", "example_id": 219, "hard_negative_for": "complaint", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "need help with the app, it keeps crashing when I try to view content", "intent": "aftercare", "notes": "app crash", "example_id": 220, "hard_negative_for": "complaint", "context": None, "length_category": "long", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # TIP_INTEREST (221–240) — hard negative vs purchase_intent, appreciation
    # ──────────────────────────────────────────────────────────────
    {"text": "can I send you a tip for being amazing?", "intent": "tip_interest", "notes": "tip + appreciation", "example_id": 221, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to tip you extra for the custom video", "intent": "tip_interest", "notes": "tip for custom", "example_id": 222, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Recent custom video received"]}, "length_category": "medium", "style": "casual"},
    {"text": "where can I send a tip?", "intent": "tip_interest", "notes": "tip logistics", "example_id": 223, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "you deserve more than what I paid, here's a tip", "intent": "tip_interest", "notes": "tip + value", "example_id": 224, "hard_negative_for": "appreciation", "context": {"conversation_history": ["Recent purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "let me send you something extra as a thank you", "intent": "tip_interest", "notes": "tip as thank you", "example_id": 225, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'd like to tip you $20 for the great content", "intent": "tip_interest", "notes": "specific tip amount", "example_id": 226, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "how do I send you a tip?", "intent": "tip_interest", "notes": "tip how-to", "example_id": 227, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I want to show my appreciation with a tip", "intent": "tip_interest", "notes": "tip + appreciation", "example_id": 228, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "you worked hard on that, let me tip you", "intent": "tip_interest", "notes": "tip for effort", "example_id": 229, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can I add a tip to my subscription?", "intent": "tip_interest", "notes": "tip on subscription", "example_id": 230, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to support you with a tip", "intent": "tip_interest", "notes": "tip as support", "example_id": 231, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "here's some extra money for being so great", "intent": "tip_interest", "notes": "tip for quality", "example_id": 232, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to send you a bonus on top of what I paid", "intent": "tip_interest", "notes": "bonus tip", "example_id": 233, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Recent purchase"]}, "length_category": "medium", "style": "neutral"},
    {"text": "do you accept tips?", "intent": "tip_interest", "notes": "tip acceptance", "example_id": 234, "hard_negative_for": "price_inquiry", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I want to tip you for the amazing custom video", "intent": "tip_interest", "notes": "tip for custom", "example_id": 235, "hard_negative_for": "purchase_intent", "context": {"conversation_history": ["Custom video received"]}, "length_category": "medium", "style": "casual"},
    {"text": "let me Venmo you a tip", "intent": "tip_interest", "notes": "tip via Venmo", "example_id": 236, "hard_negative_for": "purchase_intent", "context": None, "length_category": "short", "style": "casual"},
    {"text": "you deserve a tip for making my day", "intent": "tip_interest", "notes": "tip for impact", "example_id": 237, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to send extra money as a thank you", "intent": "tip_interest", "notes": "tip as thanks", "example_id": 238, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can I send you a tip via PayPal?", "intent": "tip_interest", "notes": "tip PayPal", "example_id": 239, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "here's a little something extra for you", "intent": "tip_interest", "notes": "tip as gift", "example_id": 240, "hard_negative_for": "appreciation", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # COMPLAINT (241–260) — hard negative vs aftercare, rejection, negotiation
    # ──────────────────────────────────────────────────────────────
    {"text": "this is not what I paid for at all", "intent": "complaint", "notes": "expectation mismatch", "example_id": 241, "hard_negative_for": "aftercare", "context": {"conversation_history": ["Previous purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "I'm really disappointed with the quality", "intent": "complaint", "notes": "quality complaint", "example_id": 242, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "you took way too long to deliver, I paid 3 days ago", "intent": "complaint", "notes": "delivery delay complaint", "example_id": 243, "hard_negative_for": "aftercare", "context": {"conversation_history": ["Purchase 3 days ago"]}, "length_category": "long", "style": "casual"},
    {"text": "this feels like a scam, I got nothing after paying", "intent": "complaint", "notes": "scam accusation", "example_id": 244, "hard_negative_for": "aftercare", "context": {"conversation_history": ["Payment made previously"]}, "length_category": "medium", "style": "casual"},
    {"text": "I expected much better than this", "intent": "complaint", "notes": "expectation failure", "example_id": 245, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "the content was nothing like what was advertised", "intent": "complaint", "notes": "misrepresentation", "example_id": 246, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I paid $50 and got low quality stuff, not worth it", "intent": "complaint", "notes": "value complaint", "example_id": 247, "hard_negative_for": "aftercare", "context": {"conversation_history": ["$50 purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "your customer service is terrible", "intent": "complaint", "notes": "service complaint", "example_id": 248, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I've been waiting for 5 days and still nothing", "intent": "complaint", "notes": "long wait complaint", "example_id": 249, "hard_negative_for": "aftercare", "context": {"conversation_history": ["5 days since purchase"]}, "length_category": "medium", "style": "casual"},
    {"text": "I want a refund, this was not worth the money", "intent": "complaint", "notes": "refund demand", "example_id": 250, "hard_negative_for": "negotiation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "the video was blurry and cut off halfway through", "intent": "complaint", "notes": "technical quality complaint", "example_id": 251, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm not happy with what I received", "intent": "complaint", "notes": "general dissatisfaction", "example_id": 252, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "this was supposed to be exclusive but I've seen it posted publicly", "intent": "complaint", "notes": "exclusivity violation", "example_id": 253, "hard_negative_for": "aftercare", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "I feel ripped off honestly", "intent": "complaint", "notes": "ripoff feeling", "example_id": 254, "hard_negative_for": "aftercare", "context": None, "length_category": "short", "style": "casual"},
    {"text": "you promised custom content and sent me generic stuff", "intent": "complaint", "notes": "promise violation", "example_id": 255, "hard_negative_for": "aftercare", "context": {"conversation_history": ["Custom content was promised"]}, "length_category": "medium", "style": "casual"},
    {"text": "I've asked for help three times and nobody's responded", "intent": "complaint", "notes": "unresponsive support", "example_id": 256, "hard_negative_for": "aftercare", "context": {"conversation_history": ["Previous support requests unanswered"]}, "length_category": "medium", "style": "casual"},
    {"text": "this was absolutely not worth the price", "intent": "complaint", "notes": "price-value mismatch", "example_id": 257, "hard_negative_for": "negotiation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm really let down by what I got", "intent": "complaint", "notes": "disappointment", "example_id": 258, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "you said it would be delivered in 24 hours, it's been a week", "intent": "complaint", "notes": "delivery promise broken", "example_id": 259, "hard_negative_for": "aftercare", "context": {"conversation_history": ["24-hour delivery promised"]}, "length_category": "long", "style": "casual"},
    {"text": "I'm filing a dispute, this is unacceptable", "intent": "complaint", "notes": "dispute threat", "example_id": 260, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "formal"},

    # ──────────────────────────────────────────────────────────────
    # CUSTOM_REQUEST (261–280) — hard negative vs content_request, purchase_intent
    # ──────────────────────────────────────────────────────────────
    {"text": "could you make a video where you say my name?", "intent": "custom_request", "notes": "name personalization", "example_id": 261, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I have a specific idea for a custom video", "intent": "custom_request", "notes": "custom idea", "example_id": 262, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "do you do personalized content? I have a special request", "intent": "custom_request", "notes": "personalized request", "example_id": 263, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can you create something unique just for me?", "intent": "custom_request", "notes": "unique creation", "example_id": 264, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want a custom video for my birthday", "intent": "custom_request", "notes": "occasion custom", "example_id": 265, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "could you wear a specific outfit in a custom video?", "intent": "custom_request", "notes": "outfit custom", "example_id": 266, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'd like to commission a custom piece", "intent": "custom_request", "notes": "commission", "example_id": 267, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "formal"},
    {"text": "I have a fantasy I'd like you to recreate in a video", "intent": "custom_request", "notes": "fantasy custom", "example_id": 268, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can you do a custom video with specific poses?", "intent": "custom_request", "notes": "pose custom", "example_id": 269, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want something made specifically for me, not your usual stuff", "intent": "custom_request", "notes": "bespoke request", "example_id": 270, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can you make a video with my name written on you?", "intent": "custom_request", "notes": "name writing custom", "example_id": 271, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'd love a custom video for our anniversary", "intent": "custom_request", "notes": "anniversary custom", "example_id": 272, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you take requests for custom content?", "intent": "custom_request", "notes": "request availability", "example_id": 273, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want a video where you do a specific dance", "intent": "custom_request", "notes": "dance custom", "example_id": 274, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "could you create a custom photoshoot with a theme I pick?", "intent": "custom_request", "notes": "themed photoshoot", "example_id": 275, "hard_negative_for": "content_request", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "I want a custom video, is that something you do?", "intent": "custom_request", "notes": "custom availability", "example_id": 276, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I have a specific scenario in mind for a custom video", "intent": "custom_request", "notes": "scenario custom", "example_id": 277, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can you make something entirely new based on my idea?", "intent": "custom_request", "notes": "idea-based custom", "example_id": 278, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want a personalized video with a message for me", "intent": "custom_request", "notes": "message custom", "example_id": 279, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you offer custom content creation?", "intent": "custom_request", "notes": "custom service inquiry", "example_id": 280, "hard_negative_for": "content_request", "context": None, "length_category": "medium", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # NEGOTIATION (281–300) — hard negative vs price_inquiry, rejection, purchase_intent
    # ──────────────────────────────────────────────────────────────
    {"text": "would you take $30 instead of $50?", "intent": "negotiation", "notes": "price counter-offer", "example_id": 281, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can we work out a deal if I buy more than one?", "intent": "negotiation", "notes": "bulk deal", "example_id": 282, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "that's too expensive for me, can you do better?", "intent": "negotiation", "notes": "price too high", "example_id": 283, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'll buy right now if you lower the price by 20%", "intent": "negotiation", "notes": "conditional discount", "example_id": 284, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "any way I can get a loyalty discount?", "intent": "negotiation", "notes": "loyalty discount", "example_id": 285, "hard_negative_for": "price_inquiry", "context": {"conversation_history": ["Repeat customer"]}, "length_category": "medium", "style": "casual"},
    {"text": "I'm a long time supporter, can I get a better price?", "intent": "negotiation", "notes": "supporter discount", "example_id": 286, "hard_negative_for": "price_inquiry", "context": {"conversation_history": ["Long-time subscriber"]}, "length_category": "medium", "style": "casual"},
    {"text": "can you throw in extras if I buy the full bundle?", "intent": "negotiation", "notes": "extras negotiation", "example_id": 287, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's the best price you can give me?", "intent": "negotiation", "notes": "best price ask", "example_id": 288, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to buy but I need a lower price", "intent": "negotiation", "notes": "price barrier", "example_id": 289, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you offer payment plans? I can't pay it all at once", "intent": "negotiation", "notes": "payment plan", "example_id": 290, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'll subscribe for a year if you give me a deal", "intent": "negotiation", "notes": "annual deal", "example_id": 291, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can you do a discount for first time buyers?", "intent": "negotiation", "notes": "new customer discount", "example_id": 292, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I found a similar creator who charges less, can you match that?", "intent": "negotiation", "notes": "price match", "example_id": 293, "hard_negative_for": "price_inquiry", "context": None, "length_category": "long", "style": "neutral"},
    {"text": "would you accept a trade instead of cash?", "intent": "negotiation", "notes": "trade offer", "example_id": 294, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to negotiate the price before I commit", "intent": "negotiation", "notes": "pre-commitment negotiation", "example_id": 295, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can you give me a student discount?", "intent": "negotiation", "notes": "student discount", "example_id": 296, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to buy but that price is steep, any flexibility?", "intent": "negotiation", "notes": "flexibility ask", "example_id": 297, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "let's make a deal, I'm ready to buy if you meet me halfway", "intent": "negotiation", "notes": "meet halfway", "example_id": 298, "hard_negative_for": "purchase_intent", "context": None, "length_category": "long", "style": "casual"},
    {"text": "I'll tip extra if you lower the base price", "intent": "negotiation", "notes": "tip + discount", "example_id": 299, "hard_negative_for": "tip_interest", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's the lowest you're willing to go?", "intent": "negotiation", "notes": "floor price", "example_id": 300, "hard_negative_for": "price_inquiry", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # HESITATION (301–320) — hard negative vs rejection, uncertain, purchase_intent
    # ──────────────────────────────────────────────────────────────
    {"text": "I'm not sure if I want to commit yet", "intent": "hesitation", "notes": "commitment hesitation", "example_id": 301, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "let me think about it a bit more", "intent": "hesitation", "notes": "thinking pause", "example_id": 302, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to but something's holding me back", "intent": "hesitation", "notes": "internal conflict", "example_id": 303, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "maybe later, I'm not ready right now", "intent": "hesitation", "notes": "deferral", "example_id": 304, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm on the fence about this", "intent": "hesitation", "notes": "undecided", "example_id": 305, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I might buy it, just not today", "intent": "hesitation", "notes": "delayed decision", "example_id": 306, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to see more samples before I decide", "intent": "hesitation", "notes": "information seeking before decision", "example_id": 307, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm thinking about it, give me a sec", "intent": "hesitation", "notes": "processing time", "example_id": 308, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I really want to but I'm not sure about the price", "intent": "hesitation", "notes": "price hesitation", "example_id": 309, "hard_negative_for": "negotiation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "let me get back to you on this", "intent": "hesitation", "notes": "deferral", "example_id": 310, "hard_negative_for": "rejection", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm debating between your bundles", "intent": "hesitation", "notes": "choice paralysis", "example_id": 311, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need to sleep on it", "intent": "hesitation", "notes": "overnight decision", "example_id": 312, "hard_negative_for": "rejection", "context": None, "length_category": "short", "style": "casual"},
    {"text": "almost ready to buy but not quite yet", "intent": "hesitation", "notes": "near-purchase", "example_id": 313, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to but I'm worried it might not be worth it", "intent": "hesitation", "notes": "value concern", "example_id": 314, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "still deciding, don't rush me", "intent": "hesitation", "notes": "patience request", "example_id": 315, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'll probably buy it, just need a minute", "intent": "hesitation", "notes": "likely buyer", "example_id": 316, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm torn between the basic and premium tiers", "intent": "hesitation", "notes": "tier indecision", "example_id": 317, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to buy but I just spent money elsewhere", "intent": "hesitation", "notes": "budget constraint hesitation", "example_id": 318, "hard_negative_for": "rejection", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "give me a day or two to decide", "intent": "hesitation", "notes": "extended decision time", "example_id": 319, "hard_negative_for": "rejection", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm 80% there but something's stopping me", "intent": "hesitation", "notes": "almost ready", "example_id": 320, "hard_negative_for": "purchase_intent", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # REJECTION (321–340) — hard negative vs hesitation, uncertain, complaint
    # ──────────────────────────────────────────────────────────────
    {"text": "no, I'm not interested anymore", "intent": "rejection", "notes": "firm rejection", "example_id": 321, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'll pass on this, thanks", "intent": "rejection", "notes": "polite rejection", "example_id": 322, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "this isn't for me", "intent": "rejection", "notes": "personal fit rejection", "example_id": 323, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "I don't want to buy anything", "intent": "rejection", "notes": "purchase rejection", "example_id": 324, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "no thank you, not for me", "intent": "rejection", "notes": "polite no", "example_id": 325, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I've changed my mind, not buying", "intent": "rejection", "notes": "changed mind", "example_id": 326, "hard_negative_for": "hesitation", "context": {"conversation_history": ["Previously interested in purchasing"]}, "length_category": "medium", "style": "casual"},
    {"text": "this doesn't match what I'm looking for", "intent": "rejection", "notes": "mismatch rejection", "example_id": 327, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm good, don't need it", "intent": "rejection", "notes": "self-sufficient rejection", "example_id": 328, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "nah I'm good", "intent": "rejection", "notes": "casual no", "example_id": 329, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "slang"},
    {"text": "I decided not to go ahead with it", "intent": "rejection", "notes": "decided no", "example_id": 330, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "not gonna buy, sorry", "intent": "rejection", "notes": "short rejection", "example_id": 331, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "slang"},
    {"text": "I'm going with someone else", "intent": "rejection", "notes": "competitor rejection", "example_id": 332, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "nope, not interested", "intent": "rejection", "notes": "firm no", "example_id": 333, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I don't think this is for me", "intent": "rejection", "notes": "gentle rejection", "example_id": 334, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm not going to purchase anything", "intent": "rejection", "notes": "purchase refusal", "example_id": 335, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "thanks but no thanks", "intent": "rejection", "notes": "polite decline", "example_id": 336, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm not buying, that's final", "intent": "rejection", "notes": "final no", "example_id": 337, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I lost interest, not purchasing", "intent": "rejection", "notes": "lost interest", "example_id": 338, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "this isn't what I want", "intent": "rejection", "notes": "want mismatch", "example_id": 339, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm going to stop here, not buying", "intent": "rejection", "notes": "session end rejection", "example_id": 340, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # UNCERTAIN (341–360) — hard negative vs hesitation, greeting, casual_chat
    # ──────────────────────────────────────────────────────────────
    {"text": "I don't really know what to say", "intent": "uncertain", "notes": "verbal uncertainty", "example_id": 341, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "hmmm not sure", "intent": "uncertain", "notes": "minimal uncertainty", "example_id": 342, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm confused about what you offer", "intent": "uncertain", "notes": "confusion", "example_id": 343, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "maybe? I guess? idk", "intent": "uncertain", "notes": "triple uncertainty", "example_id": 344, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "slang"},
    {"text": "I don't understand how this works", "intent": "uncertain", "notes": "process confusion", "example_id": 345, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "kk yeah", "intent": "uncertain", "notes": "ambiguous acknowledgment", "example_id": 346, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "slang"},
    {"text": "I'm not sure what I want", "intent": "uncertain", "notes": "goal uncertainty", "example_id": 347, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "hmm let me see", "intent": "uncertain", "notes": "browsing uncertainty", "example_id": 348, "hard_negative_for": "hesitation", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I don't know what to get", "intent": "uncertain", "notes": "choice uncertainty", "example_id": 349, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what even is this?", "intent": "uncertain", "notes": "conceptual confusion", "example_id": 350, "hard_negative_for": "content_curiosity", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm lost, can you explain?", "intent": "uncertain", "notes": "explicit confusion", "example_id": 351, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "not sure what you mean by that", "intent": "uncertain", "notes": "terminology confusion", "example_id": 352, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I think I want something but I don't know what", "intent": "uncertain", "notes": "vague desire", "example_id": 353, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "huh", "intent": "uncertain", "notes": "minimal confusion", "example_id": 354, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm not following, can you clarify?", "intent": "uncertain", "notes": "clarification request", "example_id": 355, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "wait, I'm confused about the tiers", "intent": "uncertain", "notes": "tier confusion", "example_id": 356, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I don't really get what you do", "intent": "uncertain", "notes": "service confusion", "example_id": 357, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need help understanding this", "intent": "uncertain", "notes": "help request", "example_id": 358, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I don't know what I'm looking at", "intent": "uncertain", "notes": "visual confusion", "example_id": 359, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "hmm I'm not sure about any of this", "intent": "uncertain", "notes": "general uncertainty", "example_id": 360, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # REASSURANCE (361–380) — hard negative vs uncertainty, aftercare, price_inquiry
    # ──────────────────────────────────────────────────────────────
    {"text": "is this safe? I don't want my info leaked", "intent": "reassurance", "notes": "safety concern", "example_id": 361, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "will anyone else see what I buy from you?", "intent": "reassurance", "notes": "privacy concern", "example_id": 362, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm nervous about sending payment info", "intent": "reassurance", "notes": "payment anxiety", "example_id": 363, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can I trust you with my payment details?", "intent": "reassurance", "notes": "trust concern", "example_id": 364, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is this platform secure?", "intent": "reassurance", "notes": "platform security", "example_id": 365, "hard_negative_for": "uncertain", "context": None, "length_category": "short", "style": "neutral"},
    {"text": "do you keep your subscribers' information private?", "intent": "reassurance", "notes": "subscriber privacy", "example_id": 366, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want to buy but I'm worried about privacy", "intent": "reassurance", "notes": "privacy + purchase barrier", "example_id": 367, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "will my bank statement show what I purchased?", "intent": "reassurance", "notes": "statement discretion", "example_id": 368, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is it really confidential if I send you a message?", "intent": "reassurance", "notes": "message confidentiality", "example_id": 369, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I've been burned before, how do I know this is legit?", "intent": "reassurance", "notes": "legitimacy concern", "example_id": 370, "hard_negative_for": "uncertain", "context": {"conversation_history": ["Previous bad experience with other creators"]}, "length_category": "long", "style": "casual"},
    {"text": "do you delete content if I ask you to?", "intent": "reassurance", "notes": "deletion request", "example_id": 371, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm worried about being exposed, is this anonymous?", "intent": "reassurance", "notes": "anonymity concern", "example_id": 372, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is my identity safe if I subscribe?", "intent": "reassurance", "notes": "identity safety", "example_id": 373, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I just want to make sure this is secure before I pay", "intent": "reassurance", "notes": "pre-payment security", "example_id": 374, "hard_negative_for": "hesitation", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you guarantee privacy for your customers?", "intent": "reassurance", "notes": "privacy guarantee", "example_id": 375, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm paranoid about my info being shared", "intent": "reassurance", "notes": "paranoia", "example_id": 376, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is there any risk in buying from you?", "intent": "reassurance", "notes": "risk assessment", "example_id": 377, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can I trust that you won't share our conversations?", "intent": "reassurance", "notes": "conversation privacy", "example_id": 378, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need reassurance that this is safe before I proceed", "intent": "reassurance", "notes": "explicit reassurance request", "example_id": 379, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "is my payment information encrypted?", "intent": "reassurance", "notes": "encryption concern", "example_id": 380, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # APPRECIATION (381–400) — hard negative vs relationship_building, casual_chat
    # ──────────────────────────────────────────────────────────────
    {"text": "thank you so much, you're amazing!", "intent": "appreciation", "notes": "gratitude + compliment", "example_id": 381, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I really appreciate the content, it made my day", "intent": "appreciation", "notes": "content appreciation", "example_id": 382, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "you're the best, seriously", "intent": "appreciation", "notes": "best compliment", "example_id": 383, "hard_negative_for": "relationship_building", "context": None, "length_category": "short", "style": "casual"},
    {"text": "thanks for always being so responsive", "intent": "appreciation", "notes": "service appreciation", "example_id": 384, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm so grateful for what you do", "intent": "appreciation", "notes": "deep gratitude", "example_id": 385, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "the custom video was perfect, thank you!", "intent": "appreciation", "notes": "custom appreciation", "example_id": 386, "hard_negative_for": "relationship_building", "context": {"conversation_history": ["Custom video received"]}, "length_category": "medium", "style": "casual"},
    {"text": "you always deliver quality, I appreciate it", "intent": "appreciation", "notes": "quality appreciation", "example_id": 387, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "thanks a million!", "intent": "appreciation", "notes": "exaggerated thanks", "example_id": 388, "hard_negative_for": "relationship_building", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I can't thank you enough for the bundle", "intent": "appreciation", "notes": "bundle thanks", "example_id": 389, "hard_negative_for": "relationship_building", "context": {"conversation_history": ["Bundle purchased"]}, "length_category": "medium", "style": "casual"},
    {"text": "you're incredible, thank you for everything", "intent": "appreciation", "notes": "general appreciation", "example_id": 390, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I appreciate you more than you know", "intent": "appreciation", "notes": "deep appreciation", "example_id": 391, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "thank you for making my birthday special", "intent": "appreciation", "notes": "occasion appreciation", "example_id": 392, "hard_negative_for": "relationship_building", "context": {"conversation_history": ["Birthday custom video sent"]}, "length_category": "medium", "style": "casual"},
    {"text": "you always go above and beyond, thanks!", "intent": "appreciation", "notes": "effort appreciation", "example_id": 393, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm really happy with what I got, thank you", "intent": "appreciation", "notes": "purchase satisfaction", "example_id": 394, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "thanks for being so sweet", "intent": "appreciation", "notes": "personality appreciation", "example_id": 395, "hard_negative_for": "relationship_building", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I love the content, you're so talented!", "intent": "appreciation", "notes": "talent appreciation", "example_id": 396, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "thank you for being so patient with me", "intent": "appreciation", "notes": "patience appreciation", "example_id": 397, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "you're the best creator I've ever subscribed to", "intent": "appreciation", "notes": "superlative appreciation", "example_id": 398, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I just wanted to say thanks for everything", "intent": "appreciation", "notes": "general thanks", "example_id": 399, "hard_negative_for": "relationship_building", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I appreciate the effort you put into the custom video", "intent": "appreciation", "notes": "effort appreciation", "example_id": 400, "hard_negative_for": "relationship_building", "context": {"conversation_history": ["Custom video delivered"]}, "length_category": "medium", "style": "casual"},

    # ──────────────────────────────────────────────────────────────
    # OPERATOR_REQUEST (401–420) — hard negative vs uncertain, aftercare
    # ──────────────────────────────────────────────────────────────
    {"text": "I need to talk to a real person", "intent": "operator_request", "notes": "human request", "example_id": 401, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "are you a bot or a real human?", "intent": "operator_request", "notes": "bot detection", "example_id": 402, "hard_negative_for": "uncertain", "context": None, "length_category": "short", "style": "casual"},
    {"text": "can someone from your team help me?", "intent": "operator_request", "notes": "team request", "example_id": 403, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I need to speak with support staff", "intent": "operator_request", "notes": "support request", "example_id": 404, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "connect me to a customer service rep", "intent": "operator_request", "notes": "CSR request", "example_id": 405, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "formal"},
    {"text": "I want to talk to the person behind this account", "intent": "operator_request", "notes": "behind-the-scenes request", "example_id": 406, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is there a manager I can speak to?", "intent": "operator_request", "notes": "manager escalation", "example_id": 407, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "formal"},
    {"text": "I need human assistance, not a bot", "intent": "operator_request", "notes": "anti-bot request", "example_id": 408, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "can I get help from a staff member?", "intent": "operator_request", "notes": "staff help", "example_id": 409, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want to speak to someone who can make decisions", "intent": "operator_request", "notes": "decision-maker request", "example_id": 410, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "this is automated right? I need a person", "intent": "operator_request", "notes": "automation detection", "example_id": 411, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need to escalate this to a supervisor", "intent": "operator_request", "notes": "supervisor escalation", "example_id": 412, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "formal"},
    {"text": "can you transfer me to a live agent?", "intent": "operator_request", "notes": "live agent transfer", "example_id": 413, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I want to talk to the owner of this page", "intent": "operator_request", "notes": "owner request", "example_id": 414, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "is there anyone available to chat right now?", "intent": "operator_request", "notes": "availability request", "example_id": 415, "hard_negative_for": "greeting", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I need to speak with someone about a billing issue", "intent": "operator_request", "notes": "billing escalation", "example_id": 416, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "can a real person handle my request?", "intent": "operator_request", "notes": "real person request", "example_id": 417, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I want to talk to a human, not an AI", "intent": "operator_request", "notes": "human vs AI", "example_id": 418, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "connect me with your support team", "intent": "operator_request", "notes": "support team connection", "example_id": 419, "hard_negative_for": "aftercare", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I need to speak to someone in charge", "intent": "operator_request", "notes": "authority request", "example_id": 420, "hard_negative_for": "uncertain", "context": None, "length_category": "medium", "style": "neutral"},

    # ──────────────────────────────────────────────────────────────
    # OTHER (421–440) — hard negative vs personal_disclosure, casual_chat, content_curiosity
    # ──────────────────────────────────────────────────────────────
    {"text": "what's the weather like where you are?", "intent": "other", "notes": "weather question", "example_id": 421, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you like pineapple on pizza?", "intent": "other", "notes": "random opinion question", "example_id": 422, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's your favorite movie?", "intent": "other", "notes": "movie preference", "example_id": 423, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I just finished a great book, you should read it", "intent": "other", "notes": "book recommendation", "example_id": 424, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you watch any TV shows?", "intent": "other", "notes": "TV question", "example_id": 425, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "my favorite color is blue", "intent": "other", "notes": "color preference", "example_id": 426, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "short", "style": "casual"},
    {"text": "what kind of music do you listen to?", "intent": "other", "notes": "music question", "example_id": 427, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "I'm thinking about getting a new car", "intent": "other", "notes": "unrelated thought", "example_id": 428, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's your zodiac sign?", "intent": "other", "notes": "zodiac question", "example_id": 429, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I have a exam tomorrow, wish me luck", "intent": "other", "notes": "exam luck", "example_id": 430, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's the best restaurant in your area?", "intent": "other", "notes": "restaurant recommendation", "example_id": 431, "hard_negative_for": "casual_chat", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "my dog did the funniest thing today", "intent": "other", "notes": "pet story", "example_id": 432, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "do you believe in aliens?", "intent": "other", "notes": "opinion question", "example_id": 433, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I just got back from vacation in Bali", "intent": "other", "notes": "vacation mention", "example_id": 434, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's your favorite season?", "intent": "other", "notes": "season preference", "example_id": 435, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I need to go grocery shopping later", "intent": "other", "notes": "errand mention", "example_id": 436, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's the best way to learn Python?", "intent": "other", "notes": "tech question", "example_id": 437, "hard_negative_for": "content_curiosity", "context": None, "length_category": "medium", "style": "neutral"},
    {"text": "I'm thinking about redecorating my apartment", "intent": "other", "notes": "home project", "example_id": 438, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
    {"text": "what's your favorite food?", "intent": "other", "notes": "food preference", "example_id": 439, "hard_negative_for": "casual_chat", "context": None, "length_category": "short", "style": "casual"},
    {"text": "I'm going to the gym after this", "intent": "other", "notes": "gym mention", "example_id": 440, "hard_negative_for": "personal_disclosure", "context": None, "length_category": "medium", "style": "casual"},
]


def validate_440() -> tuple[bool, str]:
    """Validate the 440-case dataset for integrity, diversity, and contamination."""
    from collections import Counter

    intents = [e["intent"] for e in VALIDATION_440]
    cnt = Counter(intents)

    # Size check
    if len(VALIDATION_440) != 440:
        return False, f"expected 440, got {len(VALIDATION_440)}"

    # Intent distribution check (20 each)
    for intent, c in cnt.items():
        if c != 20:
            return False, f"{intent} has {c} not 20"

    # Unique example_id check
    ids = [e["example_id"] for e in VALIDATION_440]
    if len(ids) != len(set(ids)):
        return False, "duplicate example_ids"

    # No overlap with reference corpus
    try:
        from commerce.intent_corpus import INTENT_CORPUS
        ref_texts = set(e["example_text"].lower().strip() for e in INTENT_CORPUS)
        val_texts = [e["text"].lower().strip() for e in VALIDATION_440]
        overlap = set(val_texts) & ref_texts
        if overlap:
            return False, f"overlap with corpus: {overlap}"
    except Exception:
        pass

    # No overlap with existing 110-case validation
    try:
        from commerce.validation_dataset import VALIDATION_DATASET
        ref_texts = set(e["text"].lower().strip() for e in VALIDATION_DATASET)
        val_texts = [e["text"].lower().strip() for e in VALIDATION_440]
        overlap = set(val_texts) & ref_texts
        if overlap:
            return False, f"overlap with 110-case validation: {overlap}"
    except Exception:
        pass

    # No duplicate within 440-case validation
    texts = [e["text"].lower().strip() for e in VALIDATION_440]
    if len(texts) != len(set(texts)):
        return False, "duplicate within 440-case validation"

    # All intents from valid set
    valid_intents = {
        "greeting", "casual_chat", "relationship_building", "personal_disclosure",
        "content_curiosity", "content_request", "price_inquiry", "purchase_intent",
        "repeat_purchase_intent", "post_purchase", "aftercare", "tip_interest",
        "complaint", "custom_request", "negotiation", "hesitation", "rejection",
        "uncertain", "reassurance", "appreciation", "operator_request", "other",
    }
    for intent in intents:
        if intent not in valid_intents:
            return False, f"invalid intent: {intent}"

    return True, "ok"

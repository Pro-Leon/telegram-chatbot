from typing import Any

import tiktoken

from db.postgres import (
    get_latest_summary,
    get_recent_messages,
    get_user,
    get_user_profile,
)
from memory.retrieval import retrieve_relevant_history

ENCODING = tiktoken.encoding_for_model("gpt-4")

TOKEN_BUDGET: dict[str, int] = {
    "system": 600,
    "profile": 250,
    "summary": 400,
    "retrieved": 500,
    "recent": 1500,
}


def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def messages_to_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(count_tokens(m.get("content", "")) for m in messages)


RETRIEVAL_TRIGGERS = (
    "remember",
    "told you",
    "said",
    "mentioned",
    "last time",
    "before",
    "earlier",
    "used to",
    "what was",
    "you said",
    "previous",
)


def should_retrieve(message: str) -> bool:
    message_lower = message.lower()
    return any(trigger in message_lower for trigger in RETRIEVAL_TRIGGERS)


def trim_to_token_budget(messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    tokens_used = 0
    for msg in reversed(messages):
        msg_tokens = count_tokens(msg["content"])
        if tokens_used + msg_tokens > budget:
            break
        result.insert(0, msg)
        tokens_used += msg_tokens
    return result


STAGE_GUIDANCE = {
    "new": "This is a new fan. Be warm and welcoming. Learn about them.",
    "warming": "Fan is getting comfortable. Build rapport and connection.",
    "engaged": "Fan is engaged and active. Deepen the connection.",
    "converted": "Fan has purchased. Maintain relationship and engagement.",
}


def format_profile(profile: dict[str, Any]) -> str:
    if not profile:
        return "No profile data yet."
    lines: list[str] = []
    for key, value in profile.items():
        if value is not None and value != "":
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) if lines else "No profile data yet."


def build_system_prompt(
    persona: str,
    user: dict[str, Any],
    profile: dict[str, Any],
) -> str:
    facts = format_profile(profile)
    funnel_stage = user.get("funnel_stage", "new")
    stage_guidance = STAGE_GUIDANCE.get(funnel_stage, "")
    first_name = user.get("first_name", "there")

    return f"""{persona}

Fan information:
Name: {first_name}
{facts}

Stage guidance: {stage_guidance}

Response rules:
- Keep replies concise (2-4 sentences)
- Reference what they've told you naturally
- Match their energy and tone
- Ask one follow-up question when appropriate
- Never sound like a template
"""


async def build_context(
    user_id: int,
    current_message: str,
    persona: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    user = await get_user(user_id)
    if user is None:
        user = {"first_name": "there", "funnel_stage": "new"}

    profile = await get_user_profile(user_id)

    system_content = build_system_prompt(persona, user, profile)
    messages.append({"role": "system", "content": system_content})

    summary = await get_latest_summary(user_id)
    if summary:
        messages.append(
            {
                "role": "system",
                "content": f"Summary of earlier conversation:\n{summary}",
            }
        )

    if should_retrieve(current_message):
        retrieved = await retrieve_relevant_history(user_id, current_message, k=3)
        if retrieved:
            retrieved_text = "\n".join(f"[Past message]: {m['content']}" for m in retrieved)
            messages.append(
                {
                    "role": "system",
                    "content": f"Relevant past exchanges:\n{retrieved_text}",
                }
            )

    recent = await get_recent_messages(user_id, limit=30)
    recent_trimmed = trim_to_token_budget(recent, TOKEN_BUDGET["recent"])

    for msg in recent_trimmed:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    return messages

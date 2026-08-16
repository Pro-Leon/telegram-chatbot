from groq import AsyncGroq

from core.config import get_settings
from db.postgres import get_latest_summary, get_recent_messages, save_summary

_settings = get_settings()
_client = AsyncGroq(api_key=_settings.openai_api_key)

SUMMARIZE_MODEL = _settings.cheap_model
MAX_SUMMARY_TOKENS = 250

SUMMARY_SYSTEM_PROMPT = """Update this conversation summary with new messages.
Keep it concise (150 words max). Focus on:
- Key facts shared
- Emotional tone and relationship development
- Topics discussed
- Any commitments or promises made
- Where the conversation left off"""


async def maybe_summarize(user_id: int, message_count: int) -> None:
    if message_count % _settings.summarize_every_n != 0:
        return

    await summarize_conversation(user_id, message_count)


async def summarize_conversation(user_id: int, message_count: int) -> str:
    existing_summary = await get_latest_summary(user_id)

    recent = await get_recent_messages(user_id, limit=_settings.summarize_every_n)

    conversation_text = "\n".join(
        f"{'Fan' if m['direction'] == 'inbound' else 'You'}: {m['content']}" for m in recent
    )

    prompt = f"""{SUMMARY_SYSTEM_PROMPT}

Existing summary:
{existing_summary or "No previous summary."}

New messages:
{conversation_text}

Write updated summary:"""

    response = await _client.chat.completions.create(
        model=SUMMARIZE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_SUMMARY_TOKENS,
    )

    new_summary = response.choices[0].message.content
    await save_summary(user_id, new_summary, message_count)
    return new_summary

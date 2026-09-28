from core.config import get_settings
from core.llm_provider import get_llm_provider
from db.postgres import get_latest_summary, get_recent_messages, save_summary

_settings = get_settings()

# Sole provider default (LLAMA_MODEL via provider); None = provider default.
SUMMARIZE_MODEL: str | None = None
MAX_SUMMARY_TOKENS = 250

SUMMARY_SYSTEM_PROMPT = """Update this conversation summary with new messages.
Keep it concise (150 words max). Focus on:
- PERSON: name, how they refer to themselves, identity context
- PREFERENCES: content interests, conversational interests, likes/dislikes, recurring themes
- RELATIONSHIP: how the conversation has evolved, current tone, important moments
- COMMERCIAL: previous purchase relationship, content discussed, rejections (only when useful)
- OPEN LOOPS: unanswered questions, promises, things the fan said they would do

Do NOT:
- Write a transcript ("Fan said X. Bot said Y.")
- Repeat information already in the existing summary
- Include temporary events as permanent facts
- Duplicate authoritative purchase/transaction state"""


async def maybe_summarize(
    user_id: int,
    message_count: int,
    creator_id: int | None = None,
    source_count: int | None = None,
) -> None:
    # P1.3a: creator-owned summarization requires creator_id �?" fail closed if missing
    if creator_id is None:
        import logging
        logging.getLogger("memory.summarizer").warning("maybe_summarize missing creator_id for user=%s - skipping (strict isolation)", user_id)
        return
    # Phase 2.4: watermark gate replaces the modulo gate. Summarize when at
    # least summarize_every_n new messages arrived since the last summary;
    # missed multiples catch up with a single summary (no silent skips).
    try:
        from db.postgres import get_summary_watermark as _watermark

        _last = await _watermark(user_id, creator_id=creator_id)
    except Exception:
        _last = None
    _last = int(_last) if _last is not None else 0
    try:
        _count = int(message_count or 0)
    except (TypeError, ValueError):
        _count = 0
    if _count - _last < _settings.summarize_every_n:
        return

    # M5: source_count is the freshness identity of this computation (message
    # count observed at post_process read time). Retries re-read, so a replay
    # honestly reflects its own observation; save_summary atomically rejects
    # sources older than the already-stored summary.
    await summarize_conversation(
        user_id,
        message_count,
        creator_id=creator_id,
        source_count=source_count if source_count is not None else message_count,
    )


async def summarize_conversation(
    user_id: int,
    message_count: int,
    creator_id: int | None = None,
    source_count: int | None = None,
) -> str:
    if creator_id is None:
        raise ValueError("creator_id is required for summarize_conversation")
    existing_summary = await get_latest_summary(user_id, creator_id=creator_id)

    recent = await get_recent_messages(user_id, limit=_settings.summarize_every_n, creator_id=creator_id)

    conversation_text = "\n".join(
        f"{'Fan' if m['direction'] == 'inbound' else 'You'}: {m['content']}" for m in recent
    )

    prompt = f"""{SUMMARY_SYSTEM_PROMPT}

Existing summary:
{existing_summary or "No previous summary."}

New messages:
{conversation_text}

Write updated summary:"""

    provider = get_llm_provider()

    try:
        new_summary = await provider.generate(
            system_instruction=SUMMARY_SYSTEM_PROMPT,
            user_content=prompt,
            model=SUMMARIZE_MODEL,
            max_output_tokens=MAX_SUMMARY_TOKENS,
            temperature=0.3,
        )
    except Exception:
        # Phase 2.4: provider failure is tagged, never silent. The watermark
        # is unchanged so the next turn retries (retry marker); the existing
        # summary (possibly empty) is returned as-is.
        import logging as _logging

        _logging.getLogger("memory.summarizer").warning(
            "summarize_conversation provider failed user=%s creator=%s count=%s — keeping watermark for retry",
            user_id,
            creator_id,
            message_count,
            exc_info=True,
        )
        try:
            from commerce.production_control import record_metric as _rc_sum_fail

            _rc_sum_fail(name="summarize_failed", creator_id=creator_id, user_id=user_id, value=1.0)
        except Exception:
            pass
        return existing_summary or ""

    await save_summary(
        user_id, new_summary, message_count,
        creator_id=creator_id, source_count=source_count,
    )
    return new_summary

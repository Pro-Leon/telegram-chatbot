import asyncio, sys
sys.path.insert(0, 'E:/chatbot')
# Ensure env var is set before importing get_settings
import os
os.environ['OLLAMA_MODEL'] = 'qwen3:4b'
os.environ['OLLAMA_TIMEOUT'] = '180'
# Clear cache
import core.config
core.config.get_settings.cache_clear()
from core.config import get_settings
s=get_settings()
print(f"settings ollama_model={s.ollama_model} provider={s.llm_provider} timeout={s.ollama_timeout}")

from core.one_call_pipeline import one_call_generation
import json

async def test(msg):
    print(f"\n=== Testing msg={msg!r} ===")
    res = await one_call_generation(
        user_id=1, creator_id=1, user_message=msg, persona="You are Sunny Skye warm", profile={}, user={"first_name":"test"}, commerce_text="", recent_messages=[], retrieved_context="", generation_id="test123"
    )
    print(f"is_valid={res.is_valid} validation_error={res.validation_error}")
    print(f"provider={res.provider_name} model={res.model_name} input={res.input_tokens} output={res.output_tokens} latency={res.latency_ms}")
    print(f"reply={res.reply[:200]!r}")
    if res.signals:
        import dataclasses
        try:
            print(f"signals primary_intent={res.signals.primary_intent} purchase_intent={res.signals.purchase_intent} explicit_content_request={res.signals.explicit_content_request} declined={res.signals.declined_recent_offer} model_uncertainty={res.signals.model_uncertainty} evidence={res.signals.evidence} intent_tags={res.signals.intent_tags}")
        except Exception as e:
            print(f"signals print failed {e}")
    print(f"quality_score={res.quality_score} flags={res.quality_flags} safety={res.safety_flags}")

async def main():
    for msg in ["hey there, how's your day going? love your vibe", "hi", "hello, how are you?", "what's up?"]:
        await test(msg)
        await asyncio.sleep(2)

asyncio.run(main())

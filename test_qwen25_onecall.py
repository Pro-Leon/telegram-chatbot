import asyncio, sys, json
sys.path.insert(0, 'E:/chatbot')
import os
os.environ['OLLAMA_MODEL']='qwen2.5:3b'
os.environ['OLLAMA_TIMEOUT']='120'
import core.config
core.config.get_settings.cache_clear()
from core.config import get_settings
s=get_settings()
print(f"settings {s.ollama_model} {s.ollama_timeout}")

from core.one_call_pipeline import one_call_generation

async def test(msg):
    print(f"\n=== Testing qwen2.5:3b msg={msg!r} ===")
    res = await one_call_generation(
        user_id=1, creator_id=1, user_message=msg,
        persona="You are Sunny Skye warm", profile={}, user={"first_name":"test"},
        commerce_text="", recent_messages=[], retrieved_context="", generation_id="test123"
    )
    print(f"is_valid={res.is_valid} validation_error={res.validation_error}")
    print(f"provider={res.provider_name} model={res.model_name} input={res.input_tokens} output={res.output_tokens} latency={res.latency_ms}")
    print(f"reply={res.reply[:200]!r}")
    if res.signals:
        print(f"signals {res.signals.model_dump() if hasattr(res.signals,'model_dump') else res.signals}")
    print(f"quality {res.quality_score} flags {res.quality_flags} safety {res.safety_flags}")

async def main():
    for msg in ["hey there, how's your day going? love your vibe", "hi", "hello, how are you?", "what's up?", "hey beautiful"]:
        await test(msg)
        await asyncio.sleep(1)

asyncio.run(main())

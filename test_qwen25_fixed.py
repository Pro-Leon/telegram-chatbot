import asyncio, sys
sys.path.insert(0, 'E:/chatbot')
import os
os.environ['OLLAMA_MODEL']='qwen2.5:3b'
os.environ['OLLAMA_TIMEOUT']='120'
import core.config
core.config.get_settings.cache_clear()
from core.config import get_settings
s=get_settings()
print(f"settings {s.ollama_model}")

from core.one_call_pipeline import one_call_generation

async def test(msg):
    print(f"\n=== {msg!r} ===")
    res=await one_call_generation(user_id=1, creator_id=1, user_message=msg, persona="You are Sunny Skye warm", profile={}, user={"first_name":"test"}, commerce_text="", recent_messages=[], retrieved_context="", generation_id="test123")
    print(f"is_valid={res.is_valid} err={res.validation_error}")
    print(f"provider {res.provider_name} model {res.model_name} input {res.input_tokens} output {res.output_tokens}")
    print(f"reply {res.reply[:100]!r}")
    if res.signals:
        print(f"signals intent_tags {res.signals.intent_tags} primary {res.signals.primary_intent} evidence {res.signals.evidence} model_uncertainty {res.signals.model_uncertainty}")

async def main():
    for msg in ["hey there, how's your day going? love your vibe", "hi", "hello, how are you?", "what's up?", "hey beautiful", "how was your day?", "I want to buy something", "can you send me the video?", "thanks so much!", "haha"]:
        await test(msg)
        await asyncio.sleep(1)

asyncio.run(main())

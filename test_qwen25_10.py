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
    res=await one_call_generation(
        user_id=1, creator_id=1, user_message=msg, persona="You are Sunny Skye warm", profile={}, user={"first_name":"test"}, commerce_text="", recent_messages=[], retrieved_context="", generation_id="test123"
    )
    return res.is_valid, res.validation_error

async def main():
    msgs=["hey there, how's your day going? love your vibe", "hi", "hello, how are you?", "what's up?", "hey beautiful", "how was your day?", "I want to buy something", "can you send me the video?", "thanks so much!", "haha"]
    valid=0
    for msg in msgs:
        is_valid, err = await test(msg)
        print(f"msg={ascii(msg)[:30]} is_valid={is_valid} err={err}")
        if is_valid:
            valid+=1
        await asyncio.sleep(1)
    print(f"\nValid {valid}/10")
    if valid==10:
        print("PASS 10/10")
    else:
        print(f"FAIL {valid}/10")

asyncio.run(main())

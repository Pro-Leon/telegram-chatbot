import asyncio, json
from core.one_call_pipeline import one_call_generation
from core.config import get_settings

async def test_one(user_msg):
    s=get_settings()
    print(f"model {s.ollama_model} provider {s.llm_provider} base {s.ollama_base_url}")
    res=await one_call_generation(user_id=1, creator_id=0, user_message=user_msg, persona="You are Sunny Skye warm", profile={}, user={"first_name":"test"}, commerce_text="", recent_messages=[], retrieved_context="", generation_id="test123")
    print(f"msg={user_msg!r} is_valid={res.is_valid} validation_error={res.validation_error} provider={res.provider_name} model={res.model_name} input={res.input_tokens} output={res.output_tokens} latency={res.latency_ms} quality={res.quality_score} reply={res.reply[:80]!r}")
    return res

async def main():
    for msg in ["hey there, how's your day going? love your vibe", "hi", "hello, how are you?", "what's up?"]:
        await test_one(msg)
        await asyncio.sleep(1)

asyncio.run(main())

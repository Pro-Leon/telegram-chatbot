import asyncio, sys, json
sys.path.insert(0, 'E:/chatbot')
import os
os.environ['OLLAMA_MODEL']='qwen3:4b'
os.environ['OLLAMA_TIMEOUT']='180'
import core.config
core.config.get_settings.cache_clear()
from core.config import get_settings
s=get_settings()
print(f"settings {s.ollama_model} {s.ollama_timeout}")

from core.one_call_pipeline import one_call_generation

async def main():
    res = await one_call_generation(
        user_id=1, creator_id=1, user_message="hey there, how's your day going? love your vibe",
        persona="You are Sunny Skye warm", profile={}, user={"first_name":"test"},
        commerce_text="", recent_messages=[], retrieved_context="", generation_id="test123"
    )
    print(f"is_valid={res.is_valid} validation_error={res.validation_error}")
    print(f"provider={res.provider_name} model={res.model_name} input={res.input_tokens} output={res.output_tokens} latency={res.latency_ms}")
    print(f"reply={res.reply[:200]!r}")
    if res.signals:
        print(f"signals {res.signals.model_dump() if hasattr(res.signals,'model_dump') else res.signals}")
    # Also try direct raw via provider for comparison
    from core.llm_provider_ollama import OllamaProvider
    prov=OllamaProvider()
    prov._model="qwen3:4b"
    prov._timeout=180
    from core.one_call import ONE_CALL_SYSTEM_PROMPT
    from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS
    from core.context_compact import build_one_call_context
    user={"first_name":"test","funnel_stage":"new"}
    profile={}
    persona="You are Sunny Skye warm"
    messages=build_one_call_context(user=user, profile=profile, persona=persona, persona_name="Sunny", commerce_text="", recent_messages=[{"direction":"inbound","content":"hey there, how's your day going? love your vibe"}], retrieved_context="")
    system=ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS
    user_content=json.dumps(messages)
    print(f"\nDirect provider test messages {len(messages)}")
    try:
        raw=await prov.generate(system_instruction=system, user_content=user_content, model="qwen3:4b", response_mime_type="application/json", max_output_tokens=400, temperature=0.7, timeout_seconds=180)
        print(f"direct raw len {len(raw)} preview {raw[:300]!r}")
        print(f"prompt {prov.last_prompt_tokens} gen {prov.last_generation_tokens}")
        try:
            j=json.loads(raw)
            print(f"direct json ok reply {j.get('reply','')[:100]!r}")
        except Exception as e:
            print(f"direct json failed {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"direct failed {e}")
    await prov.close()

asyncio.run(main())

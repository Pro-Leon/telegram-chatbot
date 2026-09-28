import asyncio, json, sys
sys.path.insert(0, 'E:/chatbot')
import os
os.environ['OLLAMA_MODEL']='qwen3:4b'
os.environ['OLLAMA_TIMEOUT']='180'
# Clear cache
import core.config
core.config.get_settings.cache_clear()
from core.config import get_settings
s=get_settings()
print(f"model {s.ollama_model} timeout {s.ollama_timeout}")

from core.llm_provider_ollama import OllamaProvider
from core.one_call import ONE_CALL_SYSTEM_PROMPT
from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS
from memory.context import build_qwen3_context
# Actually use build_one_call_context to get realistic messages
from core.context_compact import build_one_call_context

async def test_one(num_predict, think_mode):
    prov = OllamaProvider(think_mode=think_mode)
    prov._model = "qwen3:4b"
    prov._timeout = 180
    # Build realistic OneCall messages as pipeline does
    user={"first_name":"test","funnel_stage":"new"}
    profile={}
    persona="You are Sunny Skye warm"
    messages = build_one_call_context(user=user, profile=profile, persona=persona, persona_name="Sunny", commerce_text="", recent_messages=[{"direction":"inbound","content":"hey there, how's your day going? love your vibe"}], retrieved_context="")
    print(f"\n=== Test think={think_mode} num_predict={num_predict} messages={len(messages)} total chars {sum(len(m.get('content','')) for m in messages)} ===")
    system = ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS
    user_content = json.dumps(messages)
    print(f"system len {len(system)} user_content len {len(user_content)}")
    try:
        # Use provider directly with same args as pipeline
        resp = await prov.generate(system_instruction=system, user_content=user_content, model="qwen3:4b", response_mime_type="application/json", max_output_tokens=num_predict, temperature=0.7, timeout_seconds=180)
        print(f"resp len {len(resp)} preview {resp[:400]!r}")
        print(f"prompt {prov.last_prompt_tokens} gen {prov.last_generation_tokens}")
        try:
            j=json.loads(resp)
            print(f"json ok reply {j.get('reply','')[:100]!r} commerce_signals keys {list(j.get('commerce_signals',{}).keys())[:5]}")
        except Exception as e:
            print(f"json parse failed {e} resp[:500]={resp[:500]!r}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"generate failed {e}")
    await prov.close()

async def main():
    for think in [False, True]:
        for num in [400, 600, 800]:
            await test_one(num_predict=num, think_mode=think)
            await asyncio.sleep(2)

asyncio.run(main())

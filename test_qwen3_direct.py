import asyncio, sys, json
sys.path.insert(0, 'E:/chatbot')
from core.llm_provider_ollama import OllamaProvider
from core.one_call import ONE_CALL_SYSTEM_PROMPT
from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS

async def test():
    prov = OllamaProvider()
    # Override model to qwen3:4b explicitly
    prov._model = "qwen3:4b"
    print(f"Testing qwen3:4b direct")
    messages = [{"role": "user", "content": "hello"}]
    system = ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS
    user_content = json.dumps([{"role":"system","content": system}, {"role":"user","content":"hello"}])
    try:
        resp = await prov.generate(system_instruction=system, user_content=user_content, model="qwen3:4b", response_mime_type="application/json", max_output_tokens=400, temperature=0.7)
        print(f"raw resp len {len(resp)}")
        print(resp[:1000])
        print(f"prompt_tokens {prov.last_prompt_tokens} gen {prov.last_generation_tokens}")
        # Try json parse
        try:
            j=json.loads(resp)
            print("json parse ok keys", list(j.keys()))
            print("reply", j.get("reply","")[:100])
            cs=j.get("commerce_signals",{})
            print("commerce_signals keys", list(cs.keys())[:10])
        except Exception as e:
            print("json parse failed", e)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("generate failed", e)
    await prov.close()

asyncio.run(test())

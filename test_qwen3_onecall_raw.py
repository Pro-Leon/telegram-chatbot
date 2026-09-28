import asyncio, json, httpx, sys
sys.path.insert(0, 'E:/chatbot')
from core.config import get_settings
from core.one_call import ONE_CALL_SYSTEM_PROMPT
from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS

async def test_raw(think=False, model="qwen3:4b"):
    s=get_settings()
    auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
    payload={
        "model": model,
        "messages": [
            {"role":"system","content": ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS},
            {"role":"user","content": json.dumps([{"role":"system","content":"Fan: test | No profile"},{"role":"user","content":"hey there, how's your day going? love your vibe"}])}
        ],
        "stream": False,
        "format": "json",
        "think": think,
        "options": {"num_predict": 400, "num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.5}
    }
    print(f"Testing model {model} think={think} num_predict 400")
    async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=httpx.Timeout(180.0)) as client:
        resp=await client.post("/api/chat", json=payload, timeout=180)
        print(f"status {resp.status_code}")
        try:
            data=resp.json()
            msg=data.get("message",{})
            print(f"message keys {list(msg.keys())}")
            print(f"content len {len(msg.get('content',''))} thinking len {len(msg.get('thinking',''))}")
            print(f"content preview {msg.get('content','')[:500]!r}")
            print(f"thinking preview {msg.get('thinking','')[:500]!r}")
            print(f"prompt_eval {data.get('prompt_eval_count')} eval {data.get('eval_count')}")
            print(f"raw data keys {list(data.keys())}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(resp.text[:2000])

for think in [False, True]:
    print("\n"+"="*80)
    asyncio.run(test_raw(think=think, model="qwen3:4b"))
    # also test qwen2.5
    print("\n--- qwen2.5:3b think False ---")
    asyncio.run(test_raw(think=False, model="qwen2.5:3b"))
    break

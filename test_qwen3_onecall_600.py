import asyncio, json, httpx, sys
sys.path.insert(0, 'E:/chatbot')
from core.config import get_settings
from core.one_call import ONE_CALL_SYSTEM_PROMPT
from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS
from core.context_compact import build_one_call_context

async def test_direct():
    s=get_settings()
    # Build same messages as pipeline
    user={"first_name":"test","funnel_stage":"new"}
    profile={}
    persona="You are Sunny Skye warm"
    messages = build_one_call_context(user=user, profile=profile, persona=persona, persona_name="Sunny", commerce_text="", recent_messages=[{"direction":"inbound","content":"hey there, how's your day going? love your vibe"}], retrieved_context="")
    system = ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS
    user_content = json.dumps(messages)
    print(f"messages {len(messages)} system {len(system)} user_content {len(user_content)}")
    # Try direct POST with qwen3:4b think False num_predict 600
    for think in [False, True]:
        for num in [400, 600, 800]:
            print(f"\n=== think={think} num_predict={num} ===")
            payload={
                "model": "qwen3:4b",
                "messages": [{"role":"system","content": system},{"role":"user","content": user_content}],
                "stream": False,
                "format": "json",
                "think": think,
                "options": {"num_predict": num, "num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.5}
            }
            auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
            async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=httpx.Timeout(300.0)) as client:
                try:
                    import time
                    start=time.monotonic()
                    resp=await client.post("/api/chat", json=payload, timeout=300)
                    elapsed=time.monotonic()-start
                    print(f"status {resp.status_code} elapsed {elapsed:.1f}s")
                    data=resp.json()
                    msg=data.get("message",{})
                    print(f"content len {len(msg.get('content',''))} thinking len {len(msg.get('thinking',''))} prompt {data.get('prompt_eval_count')} eval {data.get('eval_count')}")
                    content=msg.get('content','')
                    print(f"content preview {content[:400]!r}")
                    try:
                        j=json.loads(content)
                        print(f"json ok reply {j.get('reply','')[:80]!r} signals keys {list(j.get('commerce_signals',{}).keys())[:5]}")
                        # Check required fields
                        cs=j.get('commerce_signals',{})
                        for f in ["explicit_content_request","declined_recent_offer","model_uncertainty","evidence","intent_tags"]:
                            print(f"  {f}: {'OK' if f in cs else 'MISSING'}")
                    except Exception as e:
                        print(f"json parse failed {e}")
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    print(f"request failed {e}")
            await asyncio.sleep(2)

asyncio.run(test_direct())

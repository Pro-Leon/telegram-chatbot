import asyncio, json, httpx, sys
sys.path.insert(0, 'E:/chatbot')
from core.config import get_settings
from core.one_call import ONE_CALL_SYSTEM_PROMPT
from core.commerce_prompt import COMMERCE_SIGNAL_INSTRUCTIONS
from core.context_compact import build_one_call_context

async def test_once():
    s=get_settings()
    user={"first_name":"test","funnel_stage":"new"}
    profile={}
    persona="You are Sunny Skye warm"
    messages = build_one_call_context(user=user, profile=profile, persona=persona, persona_name="Sunny", commerce_text="", recent_messages=[{"direction":"inbound","content":"hey there, how's your day going? love your vibe"}], retrieved_context="")
    system = ONE_CALL_SYSTEM_PROMPT + "\n\n" + COMMERCE_SIGNAL_INSTRUCTIONS
    user_content = json.dumps(messages)
    payload={
        "model": "qwen3:4b",
        "messages": [{"role":"system","content": system},{"role":"user","content": user_content}],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"num_predict": 600, "num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.5}
    }
    print(f"payload messages {len(payload['messages'])} system {len(system)} user_content {len(user_content)}")
    auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
    async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=httpx.Timeout(300.0)) as client:
        import time
        start=time.monotonic()
        resp=await client.post("/api/chat", json=payload, timeout=300)
        elapsed=time.monotonic()-start
        print(f"status {resp.status_code} elapsed {elapsed:.1f}s")
        data=resp.json()
        msg=data.get("message",{})
        content=msg.get('content','')
        thinking=msg.get('thinking','')
        # Write to file to avoid unicode print issues
        open("E:/chatbot/qwen3_raw_output.json","w",encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"content len {len(content)} thinking len {len(thinking)} prompt {data.get('prompt_eval_count')} eval {data.get('eval_count')}")
        print(f"content preview ascii {ascii(content[:300])}")
        try:
            j=json.loads(content)
            print(f"json ok")
            print(f"reply {ascii(j.get('reply','')[:100])}")
            cs=j.get('commerce_signals',{})
            print(f"cs keys {list(cs.keys())}")
            for f in ["explicit_content_request","declined_recent_offer","model_uncertainty","evidence","intent_tags"]:
                print(f"  {f} in cs? {f in cs}")
        except Exception as e:
            print(f"json parse failed {e}")
            print(f"raw content ascii {ascii(content[:500])}")

asyncio.run(test_once())

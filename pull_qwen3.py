import asyncio, httpx, json, sys
sys.path.insert(0, 'E:/chatbot')
from core.config import get_settings
s=get_settings()
print(f"Pulling qwen3:4b to {s.ollama_base_url} as {s.ollama_username}")

async def pull():
    auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
    # Use longer timeout for pull (10 minutes)
    async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=httpx.Timeout(600.0)) as client:
        print("POST /api/pull qwen3:4b ... this will stream progress")
        try:
            async with client.stream("POST", "/api/pull", json={"model": "qwen3:4b"}, timeout=httpx.Timeout(600.0)) as resp:
                print(f"status {resp.status_code}")
                if resp.status_code != 200:
                    body=await resp.aread()
                    print(f"pull failed status {resp.status_code} body {body[:2000]}")
                    return False
                count=0
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    count+=1
                    try:
                        j=json.loads(line)
                        status=j.get("status","")
                        completed=j.get("completed")
                        total=j.get("total")
                        digest=j.get("digest","")[:12] if j.get("digest") else ""
                        # Print progress
                        if status:
                            if total and completed:
                                pct= (completed/total*100) if total else 0
                                print(f"[{count}] {status} {digest} {completed}/{total} {pct:.1f}%")
                            else:
                                print(f"[{count}] {status} {digest}")
                        if "error" in j:
                            print(f"ERROR in pull: {j}")
                            return False
                    except Exception as e:
                        print(f"line parse failed {line[:200]} err {e}")
                    if count>200:
                        # Avoid flood, still continue
                        pass
                print(f"pull stream ended after {count} lines")
                return True
        except httpx.ReadTimeout as e:
            print(f"ReadTimeout {e} — pull may still be running on server, check /api/tags")
            return False
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"pull exception {e}")
            return False

# Also check disk? try to get server info? Ollama doesn't expose disk via API, but we can try to check via /api/ps or just proceed
result=asyncio.run(pull())
print(f"pull result {result}")
# Verify after
async def verify():
    auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
    async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=10) as client:
        r=await client.get('/api/tags')
        print(f"verify GET /api/tags {r.status_code}")
        try:
            j=r.json()
            for m in j.get('models',[]):
                print(f" - {m.get('name')} size {m.get('size')}")
                if m.get('name')=='qwen3:4b':
                    print("qwen3:4b FOUND after pull")
        except Exception as e:
            print(e)
asyncio.run(verify())

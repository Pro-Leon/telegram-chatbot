import asyncio, sys
sys.path.insert(0, 'E:/chatbot')
from core.llm_provider_ollama import OllamaProvider
import time

async def test():
    prov = OllamaProvider()
    prov._timeout = 180.0
    prov._model = "qwen3:4b"
    print(f"Testing qwen3:4b timeout 180")
    start=time.monotonic()
    try:
        resp = await prov.generate(system_instruction="You are helpful. Reply OK", user_content="ping", max_output_tokens=20, temperature=0.0, timeout_seconds=180)
        elapsed=time.monotonic()-start
        print(f"OK elapsed {elapsed:.1f}s resp={resp[:200]!r} prompt {prov.last_prompt_tokens} gen {prov.last_generation_tokens}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"failed {e} elapsed {time.monotonic()-start:.1f}")
    await prov.close()
    # also check ps
    import httpx
    from core.config import get_settings
    s=get_settings()
    auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
    async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=10) as client:
        r=await client.get('/api/ps')
        print(f"/api/ps {r.status_code} {r.text[:1000]}")
        r2=await client.get('/api/tags')
        print(f"tags {r2.json().get('models',[])[:1]}")

asyncio.run(test())

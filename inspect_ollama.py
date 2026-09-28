import asyncio, httpx, sys
sys.path.insert(0, 'E:/chatbot')
from core.config import get_settings
s=get_settings()
print(f"Configured OLLAMA_MODEL={s.ollama_model}")
print(f"Base URL {s.ollama_base_url} user {s.ollama_username} has_key {bool(s.ollama_api_key)}")
print(f"LLM_PATH {s.llm_path} LLM_PROVIDER {s.llm_provider} CONTEXT_ENGINE_ENABLED {s.context_engine_enabled} SAMPLE {s.context_engine_sample_rate}")

async def inspect():
    auth=httpx.BasicAuth(s.ollama_username, s.ollama_api_key) if s.ollama_api_key else None
    async with httpx.AsyncClient(base_url=s.ollama_base_url, auth=auth, timeout=10) as client:
        r=await client.get('/api/tags')
        print(f"GET /api/tags status {r.status_code}")
        try:
            j=r.json()
            print(f"models count {len(j.get('models',[]))}")
            for m in j.get('models',[]):
                print(f" - {m.get('name')} size {m.get('size')} modified {m.get('modified_at')}")
        except Exception as e:
            print("json parse failed", e, r.text[:500])
        # Try to get health via provider
        from core.llm_provider_ollama import OllamaProvider
        prov=OllamaProvider()
        h=await prov.health_check()
        print(f"health_check {h}")
        await prov.close()

asyncio.run(inspect())

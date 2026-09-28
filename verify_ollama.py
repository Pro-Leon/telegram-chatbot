import asyncio
from core.llm_provider_ollama import OllamaProvider

async def test():
    prov = OllamaProvider()
    print(f"provider base_url={prov._base_url} model={prov._model} timeout={prov._timeout}")
    try:
        resp = await prov.generate(system_instruction="You are a helpful assistant. Reply with exactly: OK", user_content="ping", max_output_tokens=50, temperature=0.0)
        print("generate ok", resp[:200])
        print("prompt_tokens", prov.last_prompt_tokens, "gen_tokens", prov.last_generation_tokens)
        # health
        h = await prov.health_check()
        print("health", h)
        await prov.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("generate failed", type(e).__name__, str(e)[:500])

asyncio.run(test())

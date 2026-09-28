"""Debug: try temperature=0 to suppress thinking."""
import asyncio
import httpx
import json
from core.config import get_settings


async def run():
    s = get_settings()
    auth = httpx.BasicAuth(
        username=s.ollama_username, password=s.ollama_api_key
    ) if s.ollama_api_key else None

    async with httpx.AsyncClient(
        base_url=s.ollama_base_url,
        timeout=httpx.Timeout(120.0), auth=auth,
    ) as c:
        # Test: temperature=0 might suppress creative thinking
        print("T1: think=false, temperature=0, num_predict=50")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Reply with exactly one word: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 50, "temperature": 0, "top_k": 1},
        })
        d = r.json()
        msg = d.get("message", {})
        content = msg.get("content", "")
        print("  content: %r" % content[:200])
        print("  eval_count: %d" % d.get("eval_count", 0))
        print("  content starts with 'OK': %s" % content.strip().startswith("OK"))

        # Test: use generate endpoint instead
        print("\nT2: /api/generate (completion endpoint)")
        r = await c.post("/api/generate", json={
            "model": "qwen3:4b",
            "prompt": "Say exactly: OK",
            "stream": False,
            "think": False,
            "options": {"num_predict": 50, "temperature": 0.7},
        })
        d = r.json()
        print("  response: %r" % d.get("response", "")[:200])
        print("  eval_count: %d" % d.get("eval_count", 0))


if __name__ == "__main__":
    asyncio.run(run())

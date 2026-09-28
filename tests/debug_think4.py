"""Debug: what if we give enough tokens for thinking + content?"""
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
        # The model thinks first, then produces content.
        # If num_predict is too small, all tokens go to thinking.
        # Test with large num_predict to get both thinking + content.

        print("T1: /no_think, num_predict=500")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nSay exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 500, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", "")[:200])
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))

        # Test: think=false with enough tokens
        print("\nT2: think=false, num_predict=500")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 500, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", "")[:200])
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))


if __name__ == "__main__":
    asyncio.run(run())

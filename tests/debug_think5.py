"""Debug: find minimum num_predict for content to appear."""
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
        # Test think=false - content should appear immediately
        print("T1: think=false, num_predict=50")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 50, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")
        print("  content: %r" % content[:200])
        print("  thinking: %r" % (thinking[:100] if thinking else None))
        print("  eval_count: %d" % d.get("eval_count", 0))
        print("  content starts with 'OK': %s" % content.strip().startswith("OK"))

        # Test think=false with shorter prompt
        print("\nT2: think=false, short system")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "user", "content": "Say OK"},
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 50, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        content = msg.get("content", "")
        print("  content: %r" % content[:200])
        print("  eval_count: %d" % d.get("eval_count", 0))


if __name__ == "__main__":
    asyncio.run(run())

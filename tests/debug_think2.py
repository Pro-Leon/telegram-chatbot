"""Debug: does /no_think work with more tokens?"""
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
        # Test with 50 tokens - enough for thinking + content
        print("T1: /no_think system, num_predict=50")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nSay exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 50, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:200] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))

        # Test with 100 tokens
        print("\nT2: /no_think system, num_predict=100")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nSay exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 100, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:200] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))

        # Test without /no_think, more tokens
        print("\nT3: No prefix, num_predict=100")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 100, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:200] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))


if __name__ == "__main__":
    asyncio.run(run())

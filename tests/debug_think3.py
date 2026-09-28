"""Debug: find how to disable thinking on native API."""
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
        # Test: think=false parameter
        print("T1: think=false")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))

        # Test: think=true to see if it works
        print("\nT2: think=true")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "think": True,
            "options": {"num_predict": 20, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:200] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))

        # Test: no think param, check default behavior
        print("\nT3: No think param (default)")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))

        # Test: what if we use a simpler system prompt without /no_think
        print("\nT4: Simple prompt, no /no_think")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "user", "content": "Say OK"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))
        print("  eval_count: %d" % d.get("eval_count", 0))


if __name__ == "__main__":
    asyncio.run(run())

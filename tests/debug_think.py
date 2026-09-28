"""Debug thinking mode with native API."""
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
        # Test 1: No prefix
        print("T1: No prefix")
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

        # Test 2: /no_think in system
        print("\nT2: /no_think in system")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nSay exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))

        # Test 3: /no_think in user message
        print("\nT3: /no_think in user message")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "/no_think\nping"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))

        # Test 4: disable_thinking parameter
        print("\nT4: chat_template_kwargs disable_thinking")
        r = await c.post("/api/chat", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "Say exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
            "think": False,
        })
        d = r.json()
        msg = d.get("message", {})
        print("  content: %r" % msg.get("content", ""))
        print("  thinking: %r" % (msg.get("thinking", "")[:100] if msg.get("thinking") else None))

        # Test 5: OpenAI endpoint for reference
        print("\nT5: OpenAI endpoint (reference)")
        r = await c.post("/v1/chat/completions", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nSay exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        d = r.json()
        c1 = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        print("  content: %r" % c1)


if __name__ == "__main__":
    asyncio.run(run())

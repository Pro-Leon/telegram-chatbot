"""Debug: test native API with think=false and larger num_predict to find when content appears."""
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
        timeout=httpx.Timeout(300.0), auth=auth,
    ) as c:
        # think=false puts thinking in content. But how many tokens of thinking?
        # Try different num_predict to see when we get "real" content

        for np in [20, 50, 100, 150, 200]:
            print("think=false, num_predict=%d" % np)
            r = await c.post("/api/chat", json={
                "model": "qwen3:4b",
                "messages": [
                    {"role": "system", "content": "Reply with exactly: OK"},
                    {"role": "user", "content": "ping"},
                ],
                "stream": False,
                "think": False,
                "options": {"num_predict": np, "temperature": 0.7},
            })
            d = r.json()
            content = d.get("message", {}).get("content", "")
            ec = d.get("eval_count", 0)
            # Check if content contains "OK" after thinking
            has_ok = "OK" in content or "Ok" in content or "ok" in content
            print("  eval=%d  has_OK=%s  content_end=%r" % (ec, has_ok, content[-80:] if content else ""))
            print()


if __name__ == "__main__":
    asyncio.run(run())

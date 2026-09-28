"""Debug Qwen3 health check."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def debug():
    from dotenv import load_dotenv
    load_dotenv(".env")

    import core.llm_provider_ollama as ollama_mod
    from core.config import get_settings
    s = get_settings()
    ollama_mod._settings = s

    from core.llm_provider_ollama import OllamaProvider
    p = OllamaProvider()
    print("Timeout:", p._timeout)

    # Step 1: Test _get_client
    client = await p._get_client()
    print("Client created, timeout:", client.timeout)

    # Step 2: Test tags
    print("Testing tags...")
    t0 = time.time()
    r = await client.get("/api/tags", timeout=15.0)
    t1 = time.time()
    print("  Tags:", r.status_code, "in", int((t1 - t0) * 1000), "ms")
    data = r.json()
    models = [m["name"] for m in data.get("models", [])]
    print("  Models:", models)
    qwen_present = "qwen3:4b" in models
    print("  qwen3:4b present:", qwen_present)

    # Step 3: Test generation via adapter
    print("Testing generation via adapter...")
    t0 = time.time()
    try:
        result = await p.generate(
            system_instruction="Reply with exactly: OK",
            user_content="ping",
            max_output_tokens=10,
            temperature=0.0,
        )
        t1 = time.time()
        print("  Result:", result)
        print("  Latency:", int((t1 - t0) * 1000), "ms")
    except Exception as e:
        t1 = time.time()
        print("  Error:", e)
        print("  Latency:", int((t1 - t0) * 1000), "ms")

    # Step 4: Manual generation via httpx
    print("Manual generation via httpx...")
    import httpx
    t0 = time.time()
    try:
        async with httpx.AsyncClient(auth=p._build_auth(), timeout=httpx.Timeout(120.0)) as c:
            payload = {
                "model": "qwen3:4b",
                "messages": [
                    {"role": "system", "content": "Reply with exactly: OK"},
                    {"role": "user", "content": "ping"},
                ],
                "stream": False,
            }
            r = await c.post(
                "https://ollama.brestalogistics.co.ke/v1/chat/completions",
                json=payload,
            )
            t1 = time.time()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print("  Content:", content)
            print("  Latency:", int((t1 - t0) * 1000), "ms")
    except Exception as e:
        t1 = time.time()
        print("  Error:", e)
        print("  Latency:", int((t1 - t0) * 1000), "ms")

    await p.close()


if __name__ == "__main__":
    asyncio.run(debug())

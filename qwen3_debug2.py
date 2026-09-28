"""Debug Qwen3 adapter response."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def debug():
    from dotenv import load_dotenv
    load_dotenv(".env")

    import httpx
    import json

    api_key = os.environ.get("OLLAMA_API_KEY", "")
    auth = httpx.BasicAuth("ollama", api_key)

    # Test 1: Via adapter's client (same as adapter does)
    print("Test 1: Via adapter's client pattern...")
    client = httpx.AsyncClient(
        base_url="https://ollama.brestalogistics.co.ke",
        timeout=httpx.Timeout(120.0),
        headers={"Content-Type": "application/json"},
        auth=auth,
    )

    payload = {
        "model": "qwen3:4b",
        "messages": [
            {"role": "system", "content": "Reply with exactly: OK"},
            {"role": "user", "content": "ping"},
        ],
        "stream": False,
    }

    t0 = time.time()
    try:
        response = await client.post(
            "/v1/chat/completions",
            json=payload,
            timeout=120.0,
        )
        t1 = time.time()
        print("  Status:", response.status_code)
        print("  Latency:", int((t1 - t0) * 1000), "ms")
        data = response.json()
        print("  Full response:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        t1 = time.time()
        print("  Error:", e)
        print("  Latency:", int((t1 - t0) * 1000), "ms")
    finally:
        await client.aclose()

    # Test 2: Via full URL (no base_url)
    print("\nTest 2: Via full URL...")
    client2 = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        auth=auth,
    )

    t0 = time.time()
    try:
        response = await client2.post(
            "https://ollama.brestalogistics.co.ke/v1/chat/completions",
            json=payload,
            timeout=120.0,
        )
        t1 = time.time()
        print("  Status:", response.status_code)
        print("  Latency:", int((t1 - t0) * 1000), "ms")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        print("  Content:", content)
    except Exception as e:
        t1 = time.time()
        print("  Error:", e)
        print("  Latency:", int((t1 - t0) * 1000), "ms")
    finally:
        await client2.aclose()


if __name__ == "__main__":
    asyncio.run(debug())

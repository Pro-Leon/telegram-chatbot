"""Debug Qwen3 adapter response - detailed."""
import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def debug():
    from dotenv import load_dotenv
    load_dotenv(".env")

    import httpx
    import json

    api_key = os.environ.get("OLLAMA_API_KEY", "")
    auth = httpx.BasicAuth("ollama", api_key)

    payload = {
        "model": "qwen3:4b",
        "messages": [
            {"role": "system", "content": "Reply with exactly: OK"},
            {"role": "user", "content": "ping"},
        ],
        "stream": False,
    }

    # Test: Via adapter's client pattern with detailed error
    print("Test: Via adapter's client pattern...")
    client = httpx.AsyncClient(
        base_url="https://ollama.brestalogistics.co.ke",
        timeout=httpx.Timeout(120.0),
        headers={"Content-Type": "application/json"},
        auth=auth,
    )

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
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "EMPTY")
        print("  Content:", content)
    except httpx.TimeoutException as e:
        t1 = time.time()
        print("  Timeout:", int((t1 - t0) * 1000), "ms")
        print("  Error:", str(e))
    except httpx.ConnectError as e:
        t1 = time.time()
        print("  ConnectError:", int((t1 - t0) * 1000), "ms")
        print("  Error:", str(e))
    except httpx.ReadError as e:
        t1 = time.time()
        print("  ReadError:", int((t1 - t0) * 1000), "ms")
        print("  Error:", str(e))
    except Exception as e:
        t1 = time.time()
        print("  Exception:", type(e).__name__, int((t1 - t0) * 1000), "ms")
        print("  Error:", str(e))
        traceback.print_exc()
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(debug())

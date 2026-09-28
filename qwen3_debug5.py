"""Debug Qwen3 adapter - trace the actual request."""
import asyncio
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def debug():
    from dotenv import load_dotenv
    load_dotenv(".env")

    import httpx

    api_key = os.environ.get("OLLAMA_API_KEY", "")
    auth = httpx.BasicAuth("ollama", api_key)

    # Simulate exactly what the adapter does
    print("Simulating adapter's generate() with max_output_tokens=10...")
    client = httpx.AsyncClient(
        base_url="https://ollama.brestalogistics.co.ke",
        timeout=httpx.Timeout(120.0),
        headers={"Content-Type": "application/json"},
        auth=auth,
    )

    thinking_prefix = "/no_think\n"
    messages = [
        {"role": "system", "content": f"{thinking_prefix}Reply with exactly: OK"},
        {"role": "user", "content": "ping"},
    ]

    payload = {
        "model": "qwen3:4b",
        "messages": messages,
        "stream": False,
    }

    # Simulate the options building
    options = {}
    max_output_tokens = 10
    temperature = 0.0
    if max_output_tokens is not None:
        options["num_predict"] = max_output_tokens
    if temperature is not None:
        options["temperature"] = temperature
    if options:
        payload["options"] = options

    print("Payload:")
    print(json.dumps(payload, indent=2))

    t0 = time.time()
    try:
        response = await client.post("/v1/chat/completions", json=payload, timeout=120.0)
        t1 = time.time()
        data = response.json()
        print(f"Status: {response.status_code}")
        print(f"Latency: {int((t1-t0)*1000)}ms")
        print(f"Full response:")
        print(json.dumps(data, indent=2))
    except Exception as e:
        t1 = time.time()
        print(f"Error: {e}")
        print(f"Latency: {int((t1-t0)*1000)}ms")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(debug())

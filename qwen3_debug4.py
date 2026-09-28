"""Debug Qwen3 adapter with /no_think."""
import asyncio
import os
import sys
import time
import json

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
    print("Model:", p._model)
    print("Timeout:", p._timeout)

    # Create a fresh client
    client = await p._get_client()
    print("Client timeout:", client.timeout)

    # Test with /no_think + num_predict
    print("\nTest 1: /no_think + num_predict=10...")
    payload = {
        "model": "qwen3:4b",
        "messages": [
            {"role": "system", "content": "/no_think\nReply with exactly: OK"},
            {"role": "user", "content": "ping"},
        ],
        "stream": False,
        "options": {"num_predict": 10, "temperature": 0.0},
    }
    t0 = time.time()
    try:
        response = await client.post("/v1/chat/completions", json=payload, timeout=120.0)
        t1 = time.time()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        print(f"  Content: '{content}'")
        print(f"  Latency: {int((t1-t0)*1000)}ms")
        print(f"  Usage: {data.get('usage', {})}")
    except Exception as e:
        t1 = time.time()
        print(f"  Error: {e}")
        print(f"  Latency: {int((t1-t0)*1000)}ms")

    # Test via adapter's generate method
    print("\nTest 2: Via adapter generate()...")
    t0 = time.time()
    try:
        result = await p.generate(
            system_instruction="Reply with exactly: OK",
            user_content="ping",
            max_output_tokens=10,
            temperature=0.0,
        )
        t1 = time.time()
        print(f"  Result: '{result}'")
        print(f"  Latency: {int((t1-t0)*1000)}ms")
    except Exception as e:
        t1 = time.time()
        print(f"  Error: {e}")
        print(f"  Latency: {int((t1-t0)*1000)}ms")

    # Test via adapter's generate method WITHOUT max_output_tokens
    print("\nTest 3: Via adapter generate() without max_output_tokens...")
    t0 = time.time()
    try:
        result = await p.generate(
            system_instruction="Reply with exactly: OK",
            user_content="ping",
            temperature=0.0,
        )
        t1 = time.time()
        print(f"  Result: '{result}'")
        print(f"  Latency: {int((t1-t0)*1000)}ms")
    except Exception as e:
        t1 = time.time()
        print(f"  Error: {e}")
        print(f"  Latency: {int((t1-t0)*1000)}ms")

    await p.close()


if __name__ == "__main__":
    asyncio.run(debug())

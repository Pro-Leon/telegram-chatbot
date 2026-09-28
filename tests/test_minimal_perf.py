"""Minimal single-request test to get any data from the VPS."""

import asyncio
import json
import time

import httpx

from core.config import get_settings


async def test():
    settings = get_settings()
    auth = None
    if settings.ollama_api_key:
        auth = httpx.BasicAuth(username=settings.ollama_username, password=settings.ollama_api_key)

    print(f"Connecting to {settings.ollama_base_url}...")

    async with httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=httpx.Timeout(300.0),
        auth=auth,
    ) as client:
        # Test network
        print("Testing network...")
        t0 = time.perf_counter()
        resp = await client.get("/api/tags", timeout=10.0)
        net = time.perf_counter() - t0
        print(f"Network RTT: {net*1000:.0f}ms")
        print(f"Tags response: {resp.json()}")

        # Minimal generation - no_think
        print("\n--- Test 1: Minimal no_think (10 tokens) ---")
        t0 = time.perf_counter()
        resp = await client.post("/v1/chat/completions", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nReply with one word."},
                {"role": "user", "content": "Hi"},
            ],
            "stream": False,
            "options": {"num_predict": 10, "temperature": 0.7},
        })
        latency = time.perf_counter() - t0
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        print(f"  Latency: {latency:.1f}s")
        print(f"  Prompt tokens: {usage.get('prompt_tokens', '?')}")
        print(f"  Completion tokens: {usage.get('completion_tokens', '?')}")
        print(f"  Response: {repr(content[:200])}")

        # Medium generation - 50 tokens
        print("\n--- Test 2: Medium no_think (50 tokens) ---")
        t0 = time.perf_counter()
        resp = await client.post("/v1/chat/completions", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/no_think\nYou are a friendly assistant. Keep replies concise."},
                {"role": "user", "content": "Hey! How are you doing today?"},
            ],
            "stream": False,
            "options": {"num_predict": 50, "temperature": 0.7},
        })
        latency = time.perf_counter() - t0
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        print(f"  Latency: {latency:.1f}s")
        print(f"  Prompt tokens: {usage.get('prompt_tokens', '?')}")
        print(f"  Completion tokens: {usage.get('completion_tokens', '?')}")
        print(f"  Response: {repr(content[:200])}")

        # CRM-style prompt
        print("\n--- Test 3: CRM-style prompt (100 tokens) ---")
        system = "/no_think\nYou are a warm, friendly assistant named Sarah. Keep replies 2-4 sentences. Never reveal you are AI."
        user = "Hey! I was thinking about going to the beach this weekend. What do you think?"
        t0 = time.perf_counter()
        resp = await client.post("/v1/chat/completions", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "num_predict": 100,
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0,
                "presence_penalty": 1.5,
            },
        })
        latency = time.perf_counter() - t0
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        print(f"  Latency: {latency:.1f}s")
        print(f"  Prompt tokens: {usage.get('prompt_tokens', '?')}")
        print(f"  Completion tokens: {usage.get('completion_tokens', '?')}")
        print(f"  Response: {repr(content[:200])}")

        # Thinking mode comparison
        print("\n--- Test 4: Thinking mode (100 tokens) ---")
        t0 = time.perf_counter()
        resp = await client.post("/v1/chat/completions", json={
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "/think\nYou are a warm, friendly assistant named Sarah."},
                {"role": "user", "content": "Hey! How are you?"},
            ],
            "stream": False,
            "options": {"num_predict": 100, "temperature": 0.6},
        })
        latency_think = time.perf_counter() - t0
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        print(f"  Latency: {latency_think:.1f}s")
        print(f"  Prompt tokens: {usage.get('prompt_tokens', '?')}")
        print(f"  Completion tokens: {usage.get('completion_tokens', '?')}")
        print(f"  Response: {repr(content[:200])}")

        print(f"\n--- Comparison ---")
        print(f"  no_think: {latency:.1f}s")
        print(f"  thinking: {latency_think:.1f}s")
        print(f"  ratio: {latency_think/latency:.1f}x")


if __name__ == "__main__":
    asyncio.run(test())

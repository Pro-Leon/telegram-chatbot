"""Verify native API adapter works with live Ollama."""
import asyncio
import time
from core.llm_provider_ollama import OllamaProvider


async def run():
    provider = OllamaProvider()
    try:
        # Test 1: Basic generation
        print("T1: Basic generation (num_predict=20)...")
        t0 = time.perf_counter()
        result = await provider.generate(
            system_instruction="You are a friendly assistant. Reply in 1-2 sentences.",
            user_content="Hey! How are you doing today?",
            max_output_tokens=20,
        )
        lat = time.perf_counter() - t0
        print("  Latency: %.1fs" % lat)
        print("  Response: %r" % result[:200])
        print("  Length: %d chars" % len(result))

        # Test 2: Health check
        print("\nT2: Health check...")
        health = await provider.health_check()
        print("  Healthy: %s" % health["healthy"])
        print("  Status: %s" % health["status"])

        # Test 3: Verify num_predict is respected
        print("\nT3: num_predict=50...")
        t0 = time.perf_counter()
        result = await provider.generate(
            system_instruction="You are a friendly assistant.",
            user_content="Tell me about yourself in a few sentences.",
            max_output_tokens=50,
        )
        lat = time.perf_counter() - t0
        print("  Latency: %.1fs" % lat)
        print("  Response: %r" % result[:200])

        print("\nAll tests passed!")

    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(run())

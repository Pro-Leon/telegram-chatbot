from core.llm_provider import get_llm_provider
from db.postgres import vector_search_messages


async def get_embedding(text: str, model: str | None = None) -> list[float]:
    """Get embedding via the sole provider.

    llama.cpp provides no embeddings endpoint, so this raises
    ``NotImplementedError`` via the provider default. Active reply
    generation and Context Engine retrieval do not require provider
    embeddings (local MiniLM handles semantic retrieval). This helper is
    retained only for legacy callers, which must treat failure as
    no-retrieval (fail-open).
    """
    provider = get_llm_provider()
    return await provider.embed(text, model=model)


async def retrieve_relevant_history(
    user_id: int,
    query: str,
    k: int = 3,
) -> list[dict]:
    try:
        query_embedding = await get_embedding(query)
    except Exception:
        # No provider embeddings (llama.cpp) — fail open with no retrieval.
        return []
    try:
        return await vector_search_messages(user_id, query_embedding, k=k)
    except Exception:
        return []

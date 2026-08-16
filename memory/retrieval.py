from openai import AsyncOpenAI

from core.config import get_settings
from db.postgres import vector_search_messages

_settings = get_settings()

_embedding_key = _settings.embedding_api_key or _settings.openai_api_key
_client = AsyncOpenAI(api_key=_embedding_key)


async def get_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    response = await _client.embeddings.create(
        model=model,
        input=text,
    )
    return response.data[0].embedding


async def retrieve_relevant_history(
    user_id: int,
    query: str,
    k: int = 3,
) -> list[dict]:
    query_embedding = await get_embedding(query)
    return await vector_search_messages(user_id, query_embedding, k=k)

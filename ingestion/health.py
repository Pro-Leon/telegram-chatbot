from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "ingestion"}


@router.get("/health/live")
async def liveness_check() -> dict[str, str]:
    return {"status": "alive"}

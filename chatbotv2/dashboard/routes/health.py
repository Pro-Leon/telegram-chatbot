"""Health and readiness routes."""

from fastapi import APIRouter
from starlette.responses import JSONResponse as _JSONResponse

from core.health import get_health_response, get_readiness_response

router = APIRouter()


@router.get("/health")
async def health():
    return get_health_response()


@router.get("/ready")
async def ready():
    resp = await get_readiness_response()
    status_code = 200 if resp["status"] == "ready" else 503
    return _JSONResponse(content=resp, status_code=status_code)

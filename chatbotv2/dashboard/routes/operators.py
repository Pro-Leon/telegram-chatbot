"""Operator list route."""

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from db.postgres import get_operator_list

router = APIRouter()


@router.get("/api/operators")
async def api_operator_list(auth: dict = Depends(require_auth)):
    operators = await get_operator_list()
    return JSONResponse(jsonable_encoder(operators))

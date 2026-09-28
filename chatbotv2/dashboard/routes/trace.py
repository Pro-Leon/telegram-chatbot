"""Read-only GenerationTrace surface — Phase 11 observability.

Strict creator-scoped read path following the live/dialog/lab route
pattern: creator resolution required (fail-closed 503), generation ID
required, explicit 404 when the generation does not exist, no global
analytics fallback, no cross-creator lookup.

The response is the bounded GenerationTrace view only: reason codes,
states, bands, IDs, hashes, versions, timestamps, bounded counts and
references. Never raw message content, drafts, intimate text,
chain-of-thought, or arbitrary explanation prose.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator

router = APIRouter()


async def _resolve_creator_or_403():
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        return None
    return ctx.creator_id


@router.get("/api/trace/{generation_id}")
async def api_generation_trace(generation_id: str, auth: dict = Depends(require_auth)):
    """Render one bounded GenerationTrace for this creator's generation."""
    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    if not isinstance(generation_id, str) or not generation_id.strip():
        return JSONResponse({"error": "generation_id required"}, status_code=400)
    try:
        from commerce.generation_trace import build_generation_trace
    except Exception:
        return JSONResponse({"error": "Trace renderer unavailable"}, status_code=503)
    try:
        trace = await build_generation_trace(creator_id, generation_id.strip())
    except ValueError:
        return JSONResponse({"error": "Invalid trace identity"}, status_code=400)
    except LookupError:
        return JSONResponse({"error": "Generation not found"}, status_code=404)
    except Exception:
        return JSONResponse({"error": "Trace assembly failed"}, status_code=500)
    return JSONResponse(trace.to_dict())

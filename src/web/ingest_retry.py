"""Authenticated, explicit recovery controls for local receipt archives."""
from starlette.responses import JSONResponse
from . import _shared as sh
from ombrebrain.storage.ingest_retry import list_receipts, start_retry

def register(mcp):
    @mcp.custom_route("/api/ingest-retries", methods=["GET"])
    async def list_pending(request):
        error = sh._require_auth(request)
        if error:
            return error
        return JSONResponse({"receipts": list_receipts()})

    @mcp.custom_route("/api/ingest-retries", methods=["POST"])
    async def retry(request):
        error = sh._require_auth(request)
        if error:
            return error
        try:
            body = await request.json()
            if not isinstance(body, dict) or body.get("confirm") is not True:
                raise ValueError("请确认使用当前配置重试")
            return JSONResponse(await start_retry(body.get("id")), status_code=202)
        except (ValueError, TypeError, FileNotFoundError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

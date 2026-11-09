"""Private operations endpoint; workspace credentials never authorize global metrics."""
import secrets

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

from ..config import settings
from ..telemetry import render

router = APIRouter()


@router.get('/metrics', include_in_schema=False)
def metrics(authorization: str | None = Header(None)):
    if not settings.metrics_token:
        raise HTTPException(404, 'Metrics disabled')
    if authorization is None:
        raise HTTPException(401, 'Scrape credentials required', headers={'WWW-Authenticate': 'Bearer'})
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(422, 'Bearer authorization required')
    if not secrets.compare_digest(token.encode(), settings.metrics_token.encode()):
        raise HTTPException(403, 'Invalid scrape credentials')
    return Response(render(), headers={'Content-Type': CONTENT_TYPE_LATEST, 'Cache-Control': 'no-store'})

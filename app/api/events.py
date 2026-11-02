"""Authenticated live events endpoint; never streams across workspace boundaries."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from ..services import events
from .principal import Principal, require_role

router = APIRouter()


@router.get('/api/v1/events')
async def stream_events(request: Request, last_event_id: str | None = Header(default=None, alias='Last-Event-ID'),
                        principal: Principal = Depends(require_role('viewer'))):
    if last_event_id is not None and not events.VALID_ID.fullmatch(last_event_id):
        raise HTTPException(422, 'Invalid Last-Event-ID')
    redis = events.redis_client()
    try:
        await redis.ping()
    except RedisError as exc:
        raise HTTPException(503, 'Live events unavailable') from exc
    finally:
        await redis.aclose()
    return StreamingResponse(
        events.listen(principal.tenant.id, last_event_id, request.is_disconnected),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no',
                 'Connection': 'keep-alive'},
    )

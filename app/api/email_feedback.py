"""Authenticated SES feedback; certificate downloads and confirmation calls are queued."""
import json
import time

from cryptography.exceptions import InvalidSignature
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from redis.exceptions import RedisError

from ..config import settings
from ..integrations import ses
from ..jobs.queue import enqueue
from ..rate_limits import RateLimiter

router = APIRouter()
certificate_limiter = RateLimiter(5, window_seconds=60, namespace='email-certificates')


@router.post('/webhooks/email')
async def email_feedback(request: Request):
    if settings.email_backend != 'ses' or not settings.email_sns_topic_arn:
        raise HTTPException(404, 'Email feedback disabled')
    raw = await request.body()
    if len(raw) > 300000:
        raise HTTPException(413, 'Notification too large')
    try:
        message = json.loads(raw)
        if not isinstance(message, dict) or not isinstance(message.get('MessageId'), str):
            raise ValueError()
        if not 1 <= len(message['MessageId']) <= 150:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid email notification') from None
    if message.get('TopicArn') != settings.email_sns_topic_arn:
        raise HTTPException(403, 'Unexpected SNS topic')
    try:
        await run_in_threadpool(ses.verify, message)
    except ses.CertificatePending:
        try:
            if await run_in_threadpool(certificate_limiter.allow, 'all'):
                identity = ses.certificate_key(message['SigningCertURL']) + ':' + str(int(time.time()) // 60)
                await enqueue('cache_email_certificate', url=message['SigningCertURL'],
                              _job_id=identity)
        except (RedisError, OSError, RuntimeError):
            pass
        raise HTTPException(503, 'Signing certificate cache warming; retry shortly',
                            headers={'Retry-After': '10'}) from None
    except RedisError:
        raise HTTPException(503, 'Email feedback temporarily unavailable') from None
    except (ValueError, TypeError, InvalidSignature):
        raise HTTPException(401, 'Invalid email notification signature') from None
    try:
        await run_in_threadpool(ses.process, message)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(422, 'Invalid SES feedback content') from None
    return {'status': 'accepted'}

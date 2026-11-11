"""Durable, conversation-bound execution. No DB session spans external IO."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import timedelta

from redis.exceptions import RedisError
from sqlalchemy import delete, select, update

from ..config import settings
from ..db import Conversation, Session, Tenant, now, uid
from ..integrations.http_actions.contracts import Definition, arguments, extract
from ..integrations.http_actions.transport import ActionError, Client
from ..models.http_actions import HttpAction as Action
from ..models.http_actions import HttpActionCall as Call
from ..rate_limits import RateLimiter
from . import crypto, email
from .audit import audit
from .email_templates import normalize


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def code_hash(identity, code):
    return hmac.new(settings.master_key.encode(), (identity + ':' + code).encode(), hashlib.sha256).hexdigest()


def scoped(db, model, tenant_id, identity, lock=False):
    query = select(model).where(model.tenant_id == tenant_id, model.id == str(identity))
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise ActionError('not_found', 404)
    return row


def configuration(row):
    private = json.loads(crypto.decrypt(row.tenant_id, row.credentials_enc))
    values = {k: v for k, v in row.definition.items() if k not in ('auth_kind', 'header_names')}
    return Definition.model_validate(values | private), private


def result(tenant_id, identity):
    with Session() as db:
        call = scoped(db, Call, tenant_id, identity)
    value = {'id': call.id, 'reference': call.id.replace('-', '')[:12], 'state': call.state,
             'error': call.last_error, 'http_status': call.http_status, 'duration_ms': call.duration_ms,
             'created_at': call.created_at.isoformat(), 'untrusted': True}
    if call.expires_at > now() and call.result_enc and (call.state == 'complete' or call.is_test):
        packet = json.loads(crypto.decrypt(tenant_id, call.result_enc))
        value['data'] = packet.get('selected')
        if call.is_test:
            value.update(raw=packet.get('raw'), identity=packet.get('identity'))
    return value


def start(tenant_id, action_id, values, conversation_id, turn_key, *, is_test=False, confirm_changes=False,
          sample_phone=None, client=None):
    with Session() as db:
        action = scoped(db, Action, tenant_id, action_id)
        if db.get(Tenant, tenant_id).suspended:
            raise ActionError('workspace_unavailable')
        if conversation_id:
            conversation = scoped(db, Conversation, tenant_id, conversation_id)
            if conversation.handoff or conversation.status != 'bot':
                raise ActionError('conversation_unavailable')
        elif not is_test:
            raise ActionError('conversation_required')
    definition, _ = configuration(action)
    values = arguments(definition, values)
    if is_test and definition.method != 'GET' and not confirm_changes:
        raise ActionError('confirm_test_changes', 422)
    packet = crypto.encrypt(tenant_id, json.dumps(values).encode())
    # A retried source turn may produce different model arguments. Never repeat a write for that turn.
    key = digest([action_id, action.revision, conversation_id, turn_key,
                  values if definition.method == 'GET' or is_test else None, is_test])
    with Session.begin() as db:
        current = scoped(db, Action, tenant_id, action_id, True)
        if current.revision != action.revision or not is_test and (
                not current.enabled or current.tested_revision != current.revision):
            raise ActionError('action_unavailable')
        previous = db.scalar(select(Call).where(Call.tenant_id == tenant_id, Call.dedupe_key == key))
        if previous:
            identity, state = previous.id, previous.state
        else:
            state = ('awaiting_confirmation' if not is_test and definition.method != 'GET'
                     and definition.customer_confirmation else 'queued')
            row = Call(id=uid(), tenant_id=tenant_id, action_id=action_id, conversation_id=conversation_id,
                revision=current.revision, dedupe_key=key, inputs_enc=packet, state=state, is_test=is_test,
                confirmed=is_test and confirm_changes, expires_at=now() + timedelta(minutes=5))
            db.add(row)
            db.flush()
            identity = row.id
            audit(db, tenant_id, 'http_action.requested', identity, actor_type='system',
                  details={'action_id': action_id, 'parameters': list(values), 'input_digest': digest(values)})
    if state == 'queued':
        return execute(tenant_id, identity, sample_phone=sample_phone, client=client)
    return result(tenant_id, identity)


def phone(value):
    value = str(value).strip().lstrip('+')
    return value if re.fullmatch(r'[1-9][0-9]{6,14}', value) else None


def selected(raw, definition):
    try:
        data = {label: extract(raw, path) for label, path in definition.outputs.items()}
        if len(json.dumps(data).encode()) > 16384:
            raise ValueError()
        return data
    except (KeyError, IndexError, TypeError, ValueError):
        raise ActionError('response_mapping_failed') from None


async def perform(definition, private, values, identity, customer_phone, call, client):
    async with asyncio.timeout(8):
        # Every URL, including OAuth and identity lookup, shares this outer deadline.
        if definition.identity == 'phone_match' and not customer_phone and not call.is_test:
            raise ActionError('verified_phone_required')
        values = values | ({'customer_phone': customer_phone} if customer_phone else {})
        lookup = definition.method != 'GET' and definition.identity != 'none'
        try:
            code, raw = await client.action(definition, private, values, identity, lookup=lookup)
        except KeyError:
            raise ActionError('parameter_unavailable') from None
        if not 200 <= code < 300:
            error = 'auth_failed' if code in (401, 403) else 'http_rejected'
            if call.is_test:
                return code, {'selected': None, 'raw': raw, 'identity': 'not_checked', 'test_error': error}, None
            raise ActionError(error, code)
        identity_state, recipient = 'not_required', None
        if definition.identity != 'none':
            try:
                found = extract(raw, definition.identity_path)
                if definition.identity == 'phone_match':
                    if not customer_phone or phone(found) != phone(customer_phone):
                        if call.is_test:
                            return code, {'selected': None, 'raw': raw, 'identity': 'mismatch',
                                          'test_error': 'identity_mismatch'}, None
                        raise ActionError('identity_mismatch', code)
                    identity_state = 'verified'
                else:
                    recipient = normalize(found)
                    identity_state = 'code_required'
            except (ValueError, KeyError, IndexError, TypeError):
                raise ActionError('identity_mismatch') from None
        if definition.identity == 'email_otp' and not call.is_test:
            if not call.otp_verified:
                return code, {'selected': selected(raw, definition) if not lookup else None,
                              'recipient_hash': digest(recipient)}, recipient
            pending = json.loads(await asyncio.to_thread(crypto.decrypt, call.tenant_id, call.result_enc))
            if pending['recipient_hash'] != digest(recipient):
                raise ActionError('identity_changed')
        if lookup:
            code, raw = await client.action(definition, private, values, identity)
            if not 200 <= code < 300:
                raise ActionError('auth_failed' if code in (401, 403) else 'http_rejected', code)
        data = selected(raw, definition)
        return code, {'selected': data if identity_state != 'code_required' or call.otp_verified else None,
                      **({'raw': raw, 'identity': identity_state} if call.is_test else {})}, None


def execute(tenant_id, identity, *, sample_phone=None, client=None):
    with Session() as db:
        snapshot = scoped(db, Call, tenant_id, identity)
        action = scoped(db, Action, tenant_id, snapshot.action_id)
    definition, private = configuration(action)
    values = json.loads(crypto.decrypt(tenant_id, snapshot.inputs_enc)) if snapshot.inputs_enc else {}
    with Session.begin() as db:
        action = scoped(db, Action, tenant_id, snapshot.action_id, True)
        call = scoped(db, Call, tenant_id, identity, True)
        if call.state != 'queued':
            run = False
        elif (call.expires_at <= now() or call.revision != action.revision or
              not call.is_test and definition.method != 'GET' and definition.customer_confirmation
              and not call.confirmed or
              not call.is_test and (not action.enabled or action.status != 'connected')):
            call.state, call.last_error = 'failed', 'action_unavailable'
            run = False
        else:
            customer_phone = sample_phone if call.is_test else None
            if call.conversation_id:
                conv = scoped(db, Conversation, tenant_id, call.conversation_id)
                if conv.handoff or conv.status != 'bot' or db.get(Tenant, tenant_id).suspended:
                    call.state, call.last_error = 'failed', 'conversation_unavailable'
                    return {'id': identity, 'state': 'failed', 'error': 'conversation_unavailable', 'untrusted': True}
                customer_phone = phone(conv.external_user) if conv.channel == 'whatsapp' else None
            call.state, run = 'running', True
            call.expires_at = now() + timedelta(minutes=2)
    if not run:
        return result(tenant_id, identity)
    began, error, status, outcome, recipient = time.monotonic(), None, None, None, None
    try:
        if not RateLimiter(settings.http_action_requests_per_minute, namespace='http-actions').allow(tenant_id):
            raise ActionError('rate_limited', 429)
        status, outcome, recipient = asyncio.run(perform(
            definition, private, values, identity, customer_phone, call, client or Client()))
        error = outcome.get('test_error')
        if (recipient and not RateLimiter(3, 300, namespace='http-action-otp')
                .allow(tenant_id + ':' + digest(recipient))):
            raise ActionError('otp_rate_limited')
    except RedisError:
        error = 'quota_unavailable'
    except ActionError as exc:
        error, status = exc.code, exc.status
    except TimeoutError:
        error = 'timeout'
    except Exception:
        error = 'execution_failed'
    if error and not call.is_test:
        outcome, recipient = None, None
    packet = crypto.encrypt(tenant_id, json.dumps(outcome).encode()) if outcome is not None else None
    otp = f'{secrets.randbelow(1000000):06d}' if recipient else None
    with Session.begin() as db:
        action = scoped(db, Action, tenant_id, snapshot.action_id, True)
        current = scoped(db, Call, tenant_id, identity, True)
        if current.state != 'running':
            return {'id': identity, 'state': current.state, 'untrusted': True}
        if action.revision != current.revision or (not current.is_test and not action.enabled):
            error, packet, recipient = 'configuration_changed', None, None
        if recipient:
            try:
                if settings.email_backend != 'ses':
                    raise ValueError('Email verification requires configured transactional delivery')
                with db.begin_nested():
                    email.sender.stage(db, recipient, 'Verify custom action', otp)
            except Exception:
                error, packet = 'email_unavailable', None
        current.http_status, current.duration_ms = status, int((time.monotonic() - began) * 1000)
        current.last_error, current.result_enc = error, packet
        uncertain = definition.method != 'GET' and (error in {
            'timeout', 'connection_failed', 'response_mapping_failed', 'configuration_changed',
            'invalid_json', 'response_too_large', 'compressed_response', 'execution_failed'}
            or error == 'http_rejected' and (status is None or status >= 500))
        current.state = ('unknown' if uncertain else 'failed' if error else
                         'awaiting_code' if recipient else 'complete')
        current.expires_at = now() + timedelta(minutes=5 if recipient else 15)
        if otp and not error:
            current.code_hash = code_hash(identity, otp)
        action.last_error = error
        if not error:
            action.last_success_at = now()
            if current.is_test and action.revision == current.revision:
                action.tested_revision, action.status = action.revision, 'connected'
        elif error == 'auth_failed':
            action.status, action.enabled = 'needs_attention', False
        audit(db, tenant_id, 'http_action.executed', identity, actor_type='system', details={
            'action_id': action.id, 'state': current.state, 'http_status': status, 'duration_ms': current.duration_ms})
    return result(tenant_id, identity)


def approve(tenant_id, conversation_id, reference, *, code=None, client=None):
    with Session.begin() as db:
        rows = list(db.scalars(select(Call).where(Call.tenant_id == tenant_id,
            Call.conversation_id == conversation_id, func_prefix(reference)).with_for_update()))
        if len(rows) != 1:
            raise ActionError('not_found', 404)
        call = rows[0]
        if call.expires_at <= now():
            raise ActionError('confirmation_expired')
        if code is None and call.state == 'awaiting_confirmation':
            call.confirmed, call.state = True, 'queued'
        elif code is not None and call.state == 'awaiting_code':
            call.code_attempts += 1
            if call.code_attempts > 5 or not hmac.compare_digest(call.code_hash or '', code_hash(call.id, code)):
                if call.code_attempts >= 5:
                    call.state, call.code_hash = 'failed', None
                # Commit failed attempts before returning; no exception rolls them back.
                return {'id': call.id, 'state': call.state, 'error': 'invalid_code', 'untrusted': True}
            call.otp_verified, call.code_hash, call.state = True, None, 'queued'
        else:
            return {'id': call.id, 'state': call.state, 'error': 'confirmation_unavailable', 'untrusted': True}
        identity = call.id
    return execute(tenant_id, identity, client=client)


def func_prefix(reference):
    from sqlalchemy import func
    if not re.fullmatch(r'[a-f0-9]{12}', reference):
        raise ActionError('not_found', 404)
    return func.replace(Call.id, '-', '').startswith(reference)


def maintain():
    with Session.begin() as db:
        db.execute(update(Call).where(Call.expires_at <= now())
                   .values(inputs_enc=None, result_enc=None, code_hash=None))
        db.execute(update(Call).where(Call.state == 'running', Call.expires_at <= now())
                   .values(state='unknown', last_error='execution_interrupted'))
        db.execute(delete(Call).where(Call.created_at < now() - timedelta(days=30)))

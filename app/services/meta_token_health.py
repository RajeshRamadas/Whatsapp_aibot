"""Check Page-token validity without retaining a Facebook user authorization."""
import logging
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidTag
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from ..config import settings
from ..db import Channel, Membership, Session, Tenant, User, now
from . import crypto, email
from .meta_connections import debug_token
from .meta_signup import MetaSignup, MetaSignupError

CHECK_INTERVAL = timedelta(days=1)
WARNING_WINDOW = timedelta(days=7)
RETRY_INTERVAL = timedelta(minutes=15)
ATTENTION = ('expiring', 'invalid')
log = logging.getLogger(__name__)


def assessment(debug, checked_at):
    if debug.get('is_valid') is False:
        return 'invalid', checked_at + CHECK_INTERVAL, None
    if debug.get('is_valid') is not True or not debug.get('app_id'):
        raise ValueError('Incomplete Meta token status')
    if str(debug['app_id']) != settings.whatsapp_app_id:
        return 'invalid', checked_at + CHECK_INTERVAL, None
    deadlines = []
    for field in ('expires_at', 'data_access_expires_at'):
        value = debug.get(field, 0)
        if type(value) is not int or value < 0:
            raise ValueError('Invalid Meta token expiry')
        if value:
            deadlines.append(datetime.fromtimestamp(value, timezone.utc))
    expiry = min(deadlines, default=None)
    next_check = checked_at + CHECK_INTERVAL
    if expiry is None:
        return 'valid', next_check, None
    if expiry <= checked_at:
        return 'invalid', next_check, expiry
    if expiry <= checked_at + WARNING_WINDOW:
        return 'expiring', min(next_check, expiry), expiry
    return 'valid', min(next_check, expiry - WARNING_WINDOW), expiry


def stage_alert(db, tenant_id):
    admins = db.scalars(select(User).join(Membership).where(
        Membership.tenant_id == tenant_id, Membership.role.in_(('owner', 'admin')),
        User.email_verified_at.is_not(None)))
    return [email.sender.stage(db, user.email, 'Meta messaging account needs attention',
        'Reauthorize the account in Messaging accounts to keep sending replies.') for user in admins]


def process_one():
    if not (settings.whatsapp_app_id and settings.whatsapp_app_secret and settings.master_key):
        return False
    claimed_at = now() + RETRY_INTERVAL
    with Session.begin() as db:
        channel = db.scalar(select(Channel).join(Tenant).where(
            Channel.kind.in_(('instagram', 'messenger')), Channel.credentials_enc.is_not(None),
            Tenant.suspended.is_(False),
            or_(Channel.meta_token_check_at.is_(None), Channel.meta_token_check_at <= now()))
            .order_by(Channel.meta_token_check_at.asc().nulls_first(), Channel.id)
            .with_for_update(of=Channel, skip_locked=True).limit(1))
        if channel is None:
            return False
        channel.meta_token_check_at = claimed_at
        identity, tenant_id, encrypted = channel.id, channel.tenant_id, channel.credentials_enc
    try:
        token = crypto.decrypt(tenant_id, encrypted).decode()
    except (ValueError, InvalidTag):
        status, next_check, expiry = 'invalid', now() + CHECK_INTERVAL, None
    else:
        try:
            status, next_check, expiry = assessment(debug_token(MetaSignup(), token), now())
        except (ValueError, OverflowError, OSError, MetaSignupError):
            # A failed probe cannot clear an existing status or known expiry.
            return True

    callbacks = []
    with Session.begin() as db:
        # Serialize status changes with the final outbound send claim and credential replacement.
        tenant = db.get(Tenant, tenant_id, with_for_update=True)
        if not tenant or tenant.suspended:
            return True
        current = db.scalar(select(Channel).where(Channel.id == identity, Channel.tenant_id == tenant_id,
            Channel.meta_token_check_at == claimed_at, Channel.credentials_enc == encrypted)
            .with_for_update())
        if not current:
            return True
        current.meta_token_status, current.meta_token_check_at = status, next_check
        current.meta_token_expires_at = expiry
        if status not in ATTENTION:
            current.meta_token_alerted = False
        elif not current.meta_token_alerted:
            try:
                with db.begin_nested():
                    staged = stage_alert(db, tenant_id)
                    current.meta_token_alerted = True
                callbacks = staged
            except (RuntimeError, ValueError, SQLAlchemyError):
                current.meta_token_check_at = min(next_check, now() + RETRY_INTERVAL)
                log.warning('meta.token_alert_unavailable')
    for deliver in callbacks:
        deliver()
    return True

from fastapi import APIRouter

from . import (
    analytics,
    api_keys,
    audit,
    auth,
    billing,
    bot,
    channels,
    chat,
    conversations,
    email_feedback,
    evals,
    events,
    inbox,
    invoices,
    jobs,
    knowledge,
    leads,
    meta_signup,
    metrics,
    onboarding,
    platform,
    push,
    qa,
    templates,
    tenants,
    usage,
    whatsapp,
    widget,
    widget_chat,
    workspaces,
)
from .shared import UPLOADS as UPLOADS
from .shared import origins as origins

router = APIRouter()
for module in (auth, audit, api_keys, workspaces, tenants, billing, channels, knowledge, onboarding, bot, jobs,
               chat, conversations, evals, events, inbox, invoices, leads, meta_signup, push, qa, templates, usage,
               whatsapp, widget, widget_chat, platform, analytics, metrics, email_feedback):
    router.include_router(module.router)

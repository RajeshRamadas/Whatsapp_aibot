from fastapi import APIRouter

from . import (
    analytics,
    api_keys,
    audit,
    auth,
    billing,
    bot,
    campaigns,
    channels,
    chat,
    contacts,
    conversations,
    email_feedback,
    evals,
    events,
    improvements,
    inbox,
    invoices,
    jobs,
    knowledge,
    leads,
    media,
    meta_signup,
    metrics,
    onboarding,
    platform,
    push,
    qa,
    templates,
    tenants,
    usage,
    websites,
    whatsapp,
    widget,
    widget_chat,
    workspaces,
)
from .shared import UPLOADS as UPLOADS
from .shared import origins as origins

router = APIRouter()
for module in (improvements, auth, audit, api_keys, workspaces, tenants, billing, channels, knowledge, onboarding, bot,
               jobs,
               chat, conversations, evals, events, inbox, invoices, leads, meta_signup, push, qa, templates, usage,
               websites,
    whatsapp, widget, widget_chat, platform, analytics, metrics, email_feedback, contacts, campaigns, media, websites):
    router.include_router(module.router)

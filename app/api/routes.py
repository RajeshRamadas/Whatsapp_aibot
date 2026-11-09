from fastapi import APIRouter

from . import (
    api_keys,
    audit,
    auth,
    billing,
    bot,
    channels,
    chat,
    conversations,
    evals,
    events,
    inbox,
    invoices,
    jobs,
    knowledge,
    leads,
    meta_signup,
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
               whatsapp, widget, widget_chat, platform):
    router.include_router(module.router)

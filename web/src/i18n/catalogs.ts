import appointments from '@/messages/appointments.en.json';
import appointmentsHi from '@/messages/appointments.hi.json';
import signupHi from '@/messages/whatsapp-signup.hi.json';
import leadsHi from '@/messages/leads.hi.json';
import mediaHi from '@/messages/media.hi.json';
import improvementsHi from '@/messages/improvements.hi.json';
import templatesHi from '@/messages/templates.hi.json';
import billingHi from '@/messages/billing.hi.json';
import analyticsHi from '@/messages/analytics.hi.json';
import invoicesHi from '@/messages/invoices.hi.json';
import platformHi from '@/messages/platform.hi.json';
import campaignsHi from '@/messages/campaigns.hi.json';
import websitesHi from '@/messages/websites.hi.json';
import inbox from '@/messages/inbox.en.json';
import inboxHi from '@/messages/inbox.hi.json';
import widget from '@/messages/widget.en.json';
import widgetHi from '@/messages/widget.hi.json';
import botMessages from '@/messages/bot-messages.en.json';
import botMessagesHi from '@/messages/bot-messages.hi.json';
import base from '@/messages/en.json';
import websites from '@/messages/websites.en.json';
import campaigns from '@/messages/campaigns.en.json';
import platform from '@/messages/platform.en.json';
import invoices from '@/messages/invoices.en.json';
import analytics from '@/messages/analytics.en.json';
import billing from '@/messages/billing.en.json';
import errors from '@/messages/errors.en.json';
import templates from '@/messages/templates.en.json';
import improvements from '@/messages/improvements.en.json';
import faq from '@/messages/faq.en.json';
import media from '@/messages/media.en.json';
import leads from '@/messages/leads.en.json';
import signup from '@/messages/whatsapp-signup.en.json';
import baseHi from '@/messages/hi.json';
import faqHi from '@/messages/faq.hi.json';
import errorsHi from '@/messages/errors.hi.json';

export const english = { appointments, inbox, widget, botMessages, base, websites, campaigns, platform, invoices, analytics, billing, errors, templates, improvements, faq, media, leads, signup };
export const hindi: typeof english = { ...english, appointments: appointmentsHi, websites: websitesHi, campaigns: campaignsHi, platform: platformHi, invoices: invoicesHi, analytics: analyticsHi, billing: billingHi, templates: templatesHi, improvements: improvementsHi, media: mediaHi, leads: leadsHi, signup: signupHi, inbox: inboxHi, widget: widgetHi, botMessages: botMessagesHi, base: baseHi, faq: faqHi, errors: errorsHi };
export type Locale = 'en' | 'hi';
export type Catalog = typeof english;
export const translatedNamespaces = Object.keys(english) as (keyof Catalog)[];
export const normalizeLocale = (value?: string): Locale => value === 'hi' ? 'hi' : 'en';
export const catalogFor = (locale: Locale): Catalog => locale === 'hi' ? hindi : english;
export function browserLocale(): Locale {
  if (typeof document === 'undefined') return 'en';
  const entry = document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith('chatbot_locale='));
  try { return normalizeLocale(decodeURIComponent(entry?.slice('chatbot_locale='.length) || '')); }
  catch { return 'en'; }
}

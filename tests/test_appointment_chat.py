"""Customer confirmations cannot be forged by model tools; reminders use real outbox state."""
from datetime import timedelta
from string import Formatter
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import rag, whatsapp
from app.db import AnswerReview, Channel, Contact, Conversation, Message, Session, Template, Tenant, engine, now, uid
from app.models.appointments import Booking, Service
from app.services import appointment_chat as chat
from app.services.appointment_reminders import process_one
from app.services.appointments import transition
from tests.test_appointments import BASE  # noqa: F401
from tests.test_appointments import setup as setup


def test_chat_hold_needs_literal_customer_confirmation_and_stays_customer_scoped(client, tenant, setup, chats):
    headers, service, staff, slot_body, slot, hold, conversations = setup
    with Session() as db:
        workspace = db.get(Tenant, tenant['tenant_id'])
    fake = chats['openai']
    fake.tool_turns = [
        lambda results, enabled: ('', [('check_slots', {})]),
        lambda results, enabled: ('', [('hold_appointment', {
            'slot_id': results[0]['result']['slots'][0]['id'], 'customer_name': hold['customer_name'],
            'customer_phone': hold['customer_phone']})]),
        lambda results, enabled: ('', [('confirm_appointment', {'confirm': True})]),
        lambda results, enabled: ('I fabricated booking success.', []),
    ]
    answer = rag.answer(workspace, conversations[0], 'मुझे अपॉइंटमेंट चाहिए', whatsapp=True)
    assert answer.appointment and 'fabricated' not in answer.text and '5 मिनट' in answer.text
    assert all(len(label) <= 20 for label in answer.choices)
    [booking] = client.get(BASE + '/bookings', headers=headers).json()
    assert booking['state'] == 'held' and not booking['reminder_opt_in'] and booking['language'] == 'hi'
    with Session.begin() as db:
        rag.save_exchange_rows(db, db.get(Conversation, conversations[0]),
            [('user', 'मुझे अपॉइंटमेंट चाहिए'), ('assistant', answer.text)], review_answer=answer)
        assert not db.scalar(select(AnswerReview)).unanswered
    ref = chat.reference(booking['id'])
    # Even a correct command cannot affect another customer's booking.
    assert chat.command(workspace, conversations[1], 'REMIND ' + ref) == chat.COPY['en']['error']
    confirmed = rag.answer(workspace, conversations[0], 'BOOK ' + ref)
    assert 'पुष्टि हुई' in confirmed.text
    [booking] = client.get(BASE + '/bookings', headers=headers).json()
    assert booking['state'] == 'confirmed' and not booking['reminder_opt_in']
    assert booking['reminder_state'] == 'skipped'
    assert 'पुष्टि हुई' in rag.answer(workspace, conversations[0], 'REMIND ' + ref).text
    assert client.get(BASE + '/bookings', headers=headers).json()[0]['reminder_opt_in']
    assert chat.command(workspace, conversations[0], 'CANCEL ' + ref).startswith('रद्द हुआ')
    assert client.get(BASE + '/bookings', headers=headers).json()[0]['state'] == 'cancelled'
    assert chat.tools(workspace, None, 'Preview booking')[0] == []


def test_appointment_catalogs_preserve_placeholders_and_fit_whatsapp():
    expected = None
    for copy in chat.COPY.values():
        keys = {key: sorted(field for _, field, _, _ in Formatter().parse(value) if field)
                for key, value in copy.items()}
        expected = expected or keys
        assert keys == expected
        held = copy['held'].format(service='S' * 100, staff='T' * 100, time='2026-12-01 09:00 Asia/Calcutta',
                                   id='abc123abc123')
        assert len(held) <= 600 and 'BOOK abc123abc123' in held and 'REMIND abc123abc123' in held


def due(client, tenant, setup, *, whatsapp_channel=False):
    headers, service, staff, slot_body, slot, hold, conversations = setup
    booking = client.post(BASE + '/bookings', headers=headers, json=hold).json()
    transition(tenant['tenant_id'], booking['id'], 'confirm')
    with Session.begin() as db:
        row = db.get(Booking, booking['id'])
        row.reminder_at = now() - timedelta(seconds=1)
        if whatsapp_channel:
            conversation = db.get(Conversation, conversations[0])
            conversation.channel, conversation.external_user = 'whatsapp', row.customer_phone.lstrip('+')
            conversation.last_inbound_at = now()
            db.get(Tenant, tenant['tenant_id']).whatsapp_phone_number_id = 'appointment-sender'
    return booking, conversations[0], headers


def test_web_reminders_are_readable_in_customer_chat_and_not_duplicated(client, tenant, setup):
    booking, conversation, headers = due(client, tenant, setup)
    assert process_one() and not process_one()
    with Session() as db:
        row = db.get(Booking, booking['id'])
        message = db.get(Message, row.reminder_message_id)
        assert row.reminder_state == 'sent' and message.role == 'agent' and 'रिमाइंडर' in message.content
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation)) == 1


def test_whatsapp_reminder_sends_outside_database_and_uncertainty_is_not_retried(client, tenant, setup, monkeypatch):
    booking, conversation, headers = due(client, tenant, setup, whatsapp_channel=True)
    calls = []
    def send(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        calls.append(True)
        raise TimeoutError('Delivery uncertain')
    monkeypatch.setattr(whatsapp, 'send_text', send)
    assert process_one() and not process_one() and len(calls) == 1
    assert client.get(BASE + '/bookings', headers=headers).json()[0]['reminder_state'] == 'unknown'
    assert client.post(BASE + '/bookings/' + booking['id'] + '/retry-reminder', headers=headers,
                       json={'confirm': True}).status_code == 409


def test_template_reminders_recheck_opt_in_approval_and_language(client, tenant, setup, monkeypatch):
    booking, conversation, headers = due(client, tenant, setup, whatsapp_channel=True)
    with Session.begin() as db:
        db.get(Conversation, conversation).last_inbound_at = now() - timedelta(days=2)
    assert process_one()
    assert client.get(BASE + '/bookings', headers=headers).json()[0]['reminder_error'] == 'template_opt_in_required'
    with Session.begin() as db:
        customer = Contact(id=uid(), tenant_id=tenant['tenant_id'], channel='whatsapp',
                           external_user=booking['customer_phone'].lstrip('+'), opt_in_at=now())
        channel = Channel(id=uid(), tenant_id=tenant['tenant_id'], kind='whatsapp', external_id='appointment-sender')
        db.add_all([customer, channel])
        db.flush()
        db.get(Conversation, conversation).contact_id = customer.id
        template = Template(id=uid(), channel_id=channel.id, meta_id='appointment-template', name='reminder',
                            language='hi', category='UTILITY', status='APPROVED', body='{{1}} — {{2}} — {{3}}')
        db.add(template)
        db.flush()
        db.get(Service, setup[1]['id']).reminder_template_id = template.id
    for change, expected in [('approval', 'approved_template_required'), ('language', 'template_language_mismatch')]:
        with Session.begin() as db:
            current = db.get(Template, template.id)
            current.status, current.language = ('PAUSED', 'hi') if change == 'approval' else ('APPROVED', 'en')
        assert client.post(BASE + '/bookings/' + booking['id'] + '/retry-reminder', headers=headers,
                           json={'confirm': True}).status_code == 200
        assert process_one()
        with Session() as db:
            assert db.get(Booking, booking['id']).reminder_error == expected
    with Session.begin() as db:
        db.get(Template, template.id).language = 'hi'
    calls = []
    def send(phone, payload, workspace):
        assert engine.pool.checkedout() == 0 and workspace == tenant['tenant_id']
        assert payload['template']['language']['code'] == 'hi'
        calls.append(True)
        return SimpleNamespace(json=lambda: {'messages': [{'id': 'wamid.appointment'}]})
    monkeypatch.setattr(whatsapp, 'graph_message', send)
    assert client.post(BASE + '/bookings/' + booking['id'] + '/retry-reminder', headers=headers,
                       json={'confirm': True}).status_code == 200
    assert process_one() and len(calls) == 1 and not process_one()
    with Session() as db:
        row = db.get(Booking, booking['id'])
        assert row.reminder_state == 'sent' and db.get(Message, row.reminder_message_id).kind == 'template'


@pytest.mark.parametrize('case,expected', [('cancel', 'cancelled'), ('suspended', 'blocked'), ('opt_out', 'blocked'),
                                          ('stale', 'unknown'), ('late', 'skipped'), ('no_consent', 'skipped')])
def test_reminders_respect_current_state_and_recover_stale_claims(client, tenant, setup, monkeypatch, case, expected):
    from app.models.appointments import Slot
    booking, conversation, headers = due(client, tenant, setup, whatsapp_channel=True)
    monkeypatch.setattr(whatsapp, 'send_text', lambda *a, **kw: pytest.fail('No reminder may be sent'))
    with Session.begin() as db:
        value = db.get(Booking, booking['id'])
        if case == 'cancel':
            value.state, value.reminder_state = 'cancelled', 'cancelled'
        elif case == 'suspended':
            db.get(Tenant, tenant['tenant_id']).suspended = True
        elif case == 'opt_out':
            contact = Contact(id=uid(), tenant_id=tenant['tenant_id'], channel='whatsapp',
                              external_user=value.customer_phone.lstrip('+'), opt_out_at=now())
            db.add(contact)
            db.flush()
            db.get(Conversation, conversation).contact_id = contact.id
        elif case == 'stale':
            value.reminder_state, value.claimed_at = 'sending', now() - timedelta(minutes=3)
        elif case == 'late':
            db.get(Slot, value.slot_id).starts_at = now() - timedelta(seconds=1)
        else:
            value.reminder_opt_in = False
    assert process_one() is (case != 'cancel')
    with Session() as db:
        assert db.get(Booking, booking['id']).reminder_state == expected
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_whatsapp_booking_notice_preserves_service_identifiers(client, tenant, setup, chats, sent):
    from tests.conftest import wa_payload
    from tests.test_whatsapp import link_number, post
    link_number(client, tenant)
    with Session.begin() as db:
        db.get(Tenant, tenant['tenant_id']).bot_live = True
        db.get(Service, setup[1]['id']).name = 'Consultation [1]'
    chats['openai'].tool_turns = [
        lambda results, enabled: ('', [('hold_appointment', {'slot_id': setup[4]['id'], 'customer_name': 'Demo'})]),
        lambda results, enabled: ('Ignored model wording', []),
    ]
    assert post(client, wa_payload('Book a consultation')).status_code == 200
    assert sent and 'Consultation [1]' in sent[-1][2] and 'BOOK ' in sent[-1][2]

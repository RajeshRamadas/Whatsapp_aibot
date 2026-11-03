"""Billing provider boundary. Tests substitute a fake at this boundary."""
from datetime import date
from typing import Protocol

from urllib.parse import urlencode

import httpx

from ..config import settings


class BillingError(RuntimeError):
    pass


class BillingProvider(Protocol):
    def create_plan(self, name: str, amount: int) -> dict: ...
    def checkout(self, plan: str, start: int | None, reference: str) -> dict: ...
    def fetch(self, subscription: str) -> dict: ...
    def settlements(self, day: date) -> list[dict]: ...
    def find_checkout(self, reference: str) -> dict | None: ...
    def change(self, subscription: str, plan: str, immediate: bool) -> dict: ...
    def cancel(self, subscription: str, at_end: bool) -> dict: ...
    def subscription_for_payment(self, payment: dict) -> str | None: ...


class Razorpay:
    def request(self, method, path, payload=None):
        try:
            response = httpx.request(method, 'https://api.razorpay.com/v1/' + path, json=payload,
                                     auth=(settings.razorpay_key_id, settings.razorpay_key_secret), timeout=15)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise BillingError('Razorpay could not complete the request') from None
        if not isinstance(data, dict):
            raise BillingError('Razorpay returned an invalid response')
        return data

    def create_plan(self, name, amount):
        return self.request('POST', 'plans', {
            'period': 'monthly', 'interval': 1, 'item': {'name': name, 'amount': amount, 'currency': 'INR'},
        })

    def checkout(self, plan, start, reference):
        payload = {'plan_id': plan, 'total_count': 120, 'quantity': 1, 'notes': {'checkout_key': reference}}
        if start is not None:
            payload['start_at'] = start
        return self.request('POST', 'subscriptions', payload)

    def fetch(self, subscription):
        return self.request('GET', 'subscriptions/' + subscription)

    def settlements(self, day):
        rows = []
        for skip in range(0, 10000, 100):
            params = {'year': day.year, 'month': day.month, 'day': day.day, 'count': 100, 'skip': skip}
            items = self.request('GET', 'settlements/recon/combined?' + urlencode(params)).get('items') or []
            rows.extend(items)
            if len(items) < 100:
                return rows
        raise BillingError('Settlement report was incomplete')

    def find_checkout(self, reference):
        for skip in range(0, 1000, 100):
            page = self.request('GET', 'subscriptions?' + urlencode({'count': 100, 'skip': skip}))
            items = page.get('items') or []
            for row in items:
                if (row.get('notes') or {}).get('checkout_key') == reference:
                    return row
            if len(items) < 100:
                break
        return None

    def change(self, subscription, plan, immediate):
        return self.request('PATCH', 'subscriptions/' + subscription, {
            'plan_id': plan, 'schedule_change_at': 'now' if immediate else 'cycle_end', 'customer_notify': True,
        })

    def cancel(self, subscription, at_end):
        return self.request('POST', 'subscriptions/' + subscription + '/cancel', {'cancel_at_cycle_end': at_end})

    def subscription_for_payment(self, payment):
        if invoice := payment.get('invoice_id'):
            return self.request('GET', 'invoices/' + invoice).get('subscription_id')
        return None


def provider() -> BillingProvider:
    if not (settings.razorpay_key_id and settings.razorpay_key_secret):
        raise BillingError('Razorpay is not configured')
    return Razorpay()

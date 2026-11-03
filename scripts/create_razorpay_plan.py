"""Create one monthly plan in the configured Razorpay account."""
import argparse
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings
from app.db import Plan, Session
from app.services.billing_provider import provider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('plan', choices=('starter', 'growth', 'business'))
    args = parser.parse_args()
    with Session() as db:
        plan = db.get(Plan, args.plan)
        amount = plan.limits['price_inr'] * 100
    issuer = settings.gst_invoice_issuer
    if issuer is not None:
        amount = int((Decimal(amount) * (1 + issuer.tax_rate / 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    result = provider().create_plan(args.plan.title(), amount)
    print(result['id'])


if __name__ == '__main__':
    main()

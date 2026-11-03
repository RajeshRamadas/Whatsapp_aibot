"""Create one monthly plan in the configured Razorpay account."""
import argparse

from app.db import Plan, Session
from app.services.billing_provider import provider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('plan', choices=('starter', 'growth', 'business'))
    args = parser.parse_args()
    with Session() as db:
        plan = db.get(Plan, args.plan)
        amount = plan.limits['price_inr'] * 100
    result = provider().create_plan(args.plan.title(), amount)
    print(result['id'])


if __name__ == '__main__':
    main()

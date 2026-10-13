from decimal import Decimal

from django.db import migrations
from django.db.models import Sum

NOTE = "Opening balance carried over when the payment ledger was introduced"
CHUNK = 500


def backfill_opening_payments(apps, schema_editor):
    """Give every sale that already has money recorded one 'opening' ledger row equal to its stored total,
    so no total changes. Safe to run twice (sales that already have an opening row are skipped). Fails, and
    so rolls everything back, if any sale's ledger would not equal its stored total afterwards."""
    Sale = apps.get_model("tracker", "Sale")
    Payment = apps.get_model("tracker", "Payment")

    pending = list(
        Sale.objects.filter(amount_paid__gt=0).exclude(payments__kind="opening")
        .values_list("pk", "company_id", "amount_paid", "fully_paid_on", "sale_date"))
    for start in range(0, len(pending), CHUNK):
        Payment.objects.bulk_create([
            Payment(company_id=company_id, sale_id=pk, kind="opening", amount=amount,
                    paid_on=fully_paid_on or sale_date, note=NOTE)
            for pk, company_id, amount, fully_paid_on, sale_date in pending[start:start + CHUNK]
        ])

    ledger = {row["sale_id"]: row["total"]
              for row in Payment.objects.values("sale_id").annotate(total=Sum("amount"))}
    wrong = [pk for pk, amount in Sale.objects.filter(amount_paid__gt=0).values_list("pk", "amount_paid")
             if ledger.get(pk, Decimal("0")) != amount]
    if wrong:
        raise RuntimeError(
            f"Opening balances do not match the stored totals for {len(wrong)} sale(s), for example sale "
            f"{wrong[0]}. Nothing was changed.")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0004_sales_ledger_and_history"),
    ]

    operations = [
        migrations.RunPython(backfill_opening_payments, migrations.RunPython.noop),
    ]

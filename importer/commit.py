from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from tracker import services
from tracker.models import Buyer, Estate, ImportBatch, Obligation, ObligationEvent, Payment, Plot, Sale, SaleEvent

from .errors import ImportStateError, StaleDryRun, UndoBlocked
from .validation import norm, validate_import


def _digits(text):
    return "".join(ch for ch in text if ch.isdigit())


@transaction.atomic
def commit_batch(batch, actor):
    """Import every row that passed validation. All or nothing."""
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.company_id != actor.company_id:
        raise PermissionDenied("Import belongs to another company.")
    if batch.status != ImportBatch.Status.VALIDATED:
        raise ImportStateError("Run a dry run on the current mapping before importing.")
    report, prepared = validate_import(batch)         # re-check against the data as it is NOW
    if not report["summary"]["can_commit"]:
        raise ImportStateError("Nothing can be imported yet: fix the mapping or map every status value.")
    expected = (batch.report or {}).get("summary", {}).get("to_import")
    if report["summary"]["to_import"] != expected:
        raise StaleDryRun("The data changed since the dry run. Run the dry run again.")

    company = batch.company
    estates = {norm(e.name): e for e in Estate.objects.filter(company=company)}
    plots_by_estate, buyers = {}, {}
    for b in Buyer.objects.filter(company=company).exclude(phone=""):
        buyers[(norm(b.full_name), _digits(b.phone))] = b
    counts = {"sales_created": 0, "estates_created": 0, "plots_created": 0, "buyers_created": 0,
              "statuses_applied": 0, "rows_skipped": report["summary"]["total_rows"] - len(prepared)}

    for row in prepared:
        estate = estates.get(row["estate_key"])
        if estate is None:
            estate = Estate.objects.create(company=company, name=row["estate"], import_batch=batch)
            estates[row["estate_key"]] = estate
            counts["estates_created"] += 1
        if estate.pk not in plots_by_estate:
            plots_by_estate[estate.pk] = {norm(p.plot_number): p for p in Plot.objects.filter(estate=estate)}
        plot = plots_by_estate[estate.pk].get(row["plot_key"])
        if plot is None:
            plot = Plot.objects.create(company=company, estate=estate, plot_number=row["plot"], import_batch=batch)
            plots_by_estate[estate.pk][row["plot_key"]] = plot
            counts["plots_created"] += 1

        buyer = None
        phone_key = (norm(row["buyer_name"]), _digits(row["phone"])) if _digits(row["phone"]) else None
        if phone_key:
            buyer = buyers.get(phone_key)
        if buyer is None:
            buyer = Buyer.objects.create(company=company, full_name=row["buyer_name"], phone=row["phone"],
                                         email=row["email"], import_batch=batch)
            counts["buyers_created"] += 1
            if phone_key:
                buyers[phone_key] = buyer

        sale = services.create_sale(company, plot, buyer, row["price"], row["sale_date"],
                                    row["amount_paid"], row["fully_paid_on"], actor=actor)
        Sale.objects.filter(pk=sale.pk).update(import_batch=batch)
        counts["sales_created"] += 1

        if row["statuses"]:
            obligations = {o.obligation_type.name: o for o in sale.obligations.select_related("obligation_type")}
            for type_name, status in row["statuses"].items():
                obligation = obligations.get(type_name)
                if obligation is None:
                    continue
                services.update_obligation(obligation, actor, status=status,
                                           note=f"Status set from import: {batch.filename}")
                counts["statuses_applied"] += 1

    batch.status = ImportBatch.Status.COMMITTED
    batch.counts = counts
    batch.committed_at = timezone.now()                # after every write: later edits will be newer than this
    batch.save(update_fields=["status", "counts", "committed_at"])
    return counts


def undo_blockers(batch):
    reasons = []
    edited = (ObligationEvent.objects
              .filter(obligation__sale__import_batch=batch, created_at__gt=batch.committed_at)
              .values("obligation_id").distinct().count())
    if edited:
        reasons.append(f"{edited} imported obligation(s) were edited after the import.")
    resold = Sale.objects.filter(plot__import_batch=batch).exclude(import_batch=batch).count()
    if resold:
        reasons.append(f"{resold} sale(s) were added to plots created by this import.")
    reused = Sale.objects.filter(buyer__import_batch=batch).exclude(import_batch=batch).count()
    if reused:
        reasons.append(f"{reused} sale(s) were added for buyers created by this import.")
    paid = (Payment.objects
            .filter(sale__import_batch=batch, created_at__gt=batch.committed_at)
            .exclude(kind=Payment.Kind.OPENING)
            .values("sale_id").distinct().count())
    if paid:
        reasons.append(f"{paid} imported sale(s) had payments recorded or changed after the import.")
    changed = (SaleEvent.objects
               .filter(sale__import_batch=batch, created_at__gt=batch.committed_at,
                       action__in=[SaleEvent.Action.BUYER_EDITED, SaleEvent.Action.CANCELLED,
                                   SaleEvent.Action.REINSTATED])
               .values("sale_id").distinct().count())
    if changed:
        reasons.append(f"{changed} imported sale(s) were edited, cancelled or reinstated after the import.")
    return reasons


@transaction.atomic
def undo_batch(batch, actor):
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.company_id != actor.company_id:
        raise PermissionDenied("Import belongs to another company.")
    if batch.status != ImportBatch.Status.COMMITTED:
        raise ImportStateError("Only an imported batch can be undone.")
    reasons = undo_blockers(batch)
    if reasons:
        raise UndoBlocked(reasons)
    Sale.objects.filter(import_batch=batch).delete()           # obligations, payments and history go with them
    Plot.objects.filter(import_batch=batch).delete()
    Buyer.objects.filter(import_batch=batch).delete()
    Estate.objects.filter(import_batch=batch, plots__isnull=True).delete()   # keep an estate that gained other plots
    batch.status = ImportBatch.Status.UNDONE
    batch.undone_at = timezone.now()
    batch.save(update_fields=["status", "undone_at"])

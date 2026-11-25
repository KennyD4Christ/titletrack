from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import PROTECT, RESTRICT, Exists, OuterRef
from django.utils import timezone

from tracker import services
from tracker.canonical import canonical_key
from tracker.models import (
    Buyer, Estate, ImportBatch, InventoryEvent, Obligation, ObligationEvent, Payment, Plot, Sale, SaleDocument,
    SaleEvent,
)

from .errors import ImportStateError, StaleDryRun, UndoBlocked
from .validation import norm, validate_import


def _digits(text):
    return "".join(ch for ch in text if ch.isdigit())


def _create_or_reuse_estate(company, name, batch):
    """Create the estate, or return the one that already has this canonical name.

    The caller looked the estate up a moment ago and found nothing. If another writer has created it since, the
    database refuses a second estate with the same key; the one that won is used, which is exactly what would have
    happened had it existed when the import began. The create has its own savepoint, so the refusal does not abort
    the import's transaction. Returns (estate, created)."""
    try:
        with transaction.atomic():
            return Estate.objects.create(company=company, name=name, import_batch=batch), True
    except IntegrityError:
        winner = Estate.objects.filter(company=company, name_key=canonical_key(name)).first()
        if winner is None:
            raise
        return winner, False


def _create_or_reuse_plot(company, estate, number, batch):
    """The same for a plot, within its estate. Returns (plot, created)."""
    try:
        with transaction.atomic():
            return Plot.objects.create(company=company, estate=estate, plot_number=number, import_batch=batch), True
    except IntegrityError:
        winner = Plot.objects.filter(estate=estate, plot_key=canonical_key(number)).first()
        if winner is None:
            raise
        return winner, False


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
            estate, created = _create_or_reuse_estate(company, row["estate"], batch)
            estates[row["estate_key"]] = estate
            counts["estates_created"] += int(created)
        if estate.pk not in plots_by_estate:
            plots_by_estate[estate.pk] = {norm(p.plot_number): p for p in Plot.objects.filter(estate=estate)}
        plot = plots_by_estate[estate.pk].get(row["plot_key"])
        if plot is None:
            plot, created = _create_or_reuse_plot(company, estate, row["plot"], batch)
            plots_by_estate[estate.pk][row["plot_key"]] = plot
            counts["plots_created"] += int(created)

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

        try:
            # create_sale locks the plot and checks it is still the plot this row named, like every other sale.
            sale = services.create_sale(company, plot, buyer, row["price"], row["sale_date"],
                                        row["amount_paid"], row["fully_paid_on"], actor=actor,
                                        expected_plot_number=row["plot"])
        except services.PlotChanged as exc:
            raise StaleDryRun(f"Row {row['row']}: {exc} Run the dry run again.") from exc
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
                       action__in=[
                           SaleEvent.Action.BUYER_EDITED,
                           SaleEvent.Action.CANCELLED,
                           SaleEvent.Action.REINSTATED,
                           SaleEvent.Action.DOCUMENT_ADDED,
                           SaleEvent.Action.DOCUMENT_ISSUED,
                           SaleEvent.Action.DOCUMENT_ISSUANCE_CORRECTED,
                           SaleEvent.Action.DOCUMENT_NOT_APPLICABLE,
                       ])
               .values("sale_id").distinct().count())
    if changed:
        reasons.append(f"{changed} imported sale(s) had changes after the import.")
    documented = SaleDocument.objects.filter(sale__import_batch=batch).values("sale_id").distinct().count()
    if documented:
        reasons.append(f"{documented} imported sale(s) have documents that were added after the import.")
    reasons.extend(_inventory_blockers(batch))
    return reasons


def _protected_references(model, ids, ignore=()):
    """Records, in models other than those in `ignore`, that point at these rows with PROTECT or RESTRICT, as
    [(plural name, how many)]. Found from the models themselves, so a feature that later adds a protected
    reference to a plot or an estate (a site plan's regions, say) blocks undo without this function changing.
    Sales are ignored where the caller handles them: undo deletes the imported ones first, and resales have
    their own blocker."""
    ids = list(ids)
    found = []
    if not ids:
        return found
    for relation in model._meta.related_objects:
        if relation.related_model in ignore or getattr(relation, "on_delete", None) not in (PROTECT, RESTRICT):
            continue
        count = relation.related_model._base_manager.filter(**{f"{relation.field.name}__in": ids}).count()
        if count:
            found.append((relation.related_model._meta.verbose_name_plural, count))
    return found


def _inventory_blockers(batch):
    """Reasons undo must refuse because of the estate and plot inventory (three kinds).

    1. Plots this import created are referenced by something that must be kept (PROTECT).
    2. Estates this import created, and that undo would delete, are referenced the same way.
    3. Someone renamed or edited an estate or plot this import created, after the import: undo would delete a
       record a person has since curated. (Registering NEW plots in an imported estate is not a blocker: undo keeps
       an estate that has other plots, and never touches plots it did not create.)"""
    reasons = []
    plot_ids = Plot.objects.filter(import_batch=batch).values_list("pk", flat=True)
    for name, count in _protected_references(Plot, plot_ids, ignore={Sale}):
        reasons.append(f"{count} {name} refer to plots this import created, so those plots cannot be removed.")
    # Only estates undo would actually delete: created by this import and left with no plot of another origin.
    other_plots = Plot.objects.filter(estate=OuterRef("pk")).exclude(import_batch=batch)
    doomed = (Estate.objects.filter(import_batch=batch).annotate(has_other=Exists(other_plots))
              .filter(has_other=False).values_list("pk", flat=True))
    for name, count in _protected_references(Estate, doomed, ignore={Plot}):
        reasons.append(f"{count} {name} refer to estates this import created, so those estates cannot be removed.")

    after = batch.committed_at
    estates = (InventoryEvent.objects.filter(created_at__gt=after, action=InventoryEvent.Action.ESTATE_EDITED,
                                             estate__import_batch=batch).values("estate_id").distinct().count())
    plots = (InventoryEvent.objects.filter(created_at__gt=after, action=InventoryEvent.Action.PLOT_RENAMED,
                                           plot__import_batch=batch).values("plot_id").distinct().count())
    if estates or plots:
        reasons.append(f"{estates + plots} imported estate(s) or plot(s) were renamed or edited after the import.")
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

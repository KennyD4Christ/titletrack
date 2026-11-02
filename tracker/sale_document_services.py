"""Sale document services (Phase 1C-2a). The ONLY way sale documents should be changed.

A SaleDocument says whether one catalogue document (DocumentType) is OWED to the buyer of a sale, has been
ISSUED, or is NOT_APPLICABLE. An owed document is backed by an Obligation (the work of preparing it) and keeps
that link for good. Preparing the document (completing the obligation) is NOT issuing it: issuance is its own
action, with its own date, and is the only way a document becomes issued.

Rules implemented here:

- Every write locks the sale first, then the document, then its obligation, so concurrent requests take turns.
  The unique (sale, document type) rule and an explicit check stop two requests adding the same document.
- Company checks everywhere: the actor, the sale, the catalogue entry, the document and its obligation must
  all belong to one company (PermissionDenied otherwise). A document's obligation must also belong to the
  same sale and to a work type of the document's own catalogue entry; if it does not, the record is refused
  as inconsistent and nothing is written. A client never supplies an obligation: it is found or created here.
- A cancelled sale's documents are read-only.
- Adding an OWED document: the sale's existing obligation for the same catalogue entry (whichever of the
  entry's work types it was created under) is ADOPTED, never duplicated, on a legacy sale, but only while that
  obligation is still OPEN. A completed or not-applicable one is refused and left exactly as it is: it may
  already stand for a document that was issued or delivered, so it must be reviewed, not reopened. On a
  document-managed sale an existing obligation should not exist and the request is refused. With none, an
  obligation is created under the entry's CURRENT work type (started today, due after that type's SLA,
  default department).
- Adding an ISSUED document needs an issue date and creates no obligation. If the sale already has an
  obligation for that entry it is refused: add the document as owed, then issue it.
- Issuing closes an open obligation through update_obligation; an already-completed one only gets a history
  note. Marking not applicable closes an open obligation the same way; a completed one is left as it is.
- A correction changes the issue date and/or reference, needs a reason, keeps who first issued the document and
  when, and records before/after values and the correcting user.
- Every step writes a SaleEvent (and an ObligationEvent where an obligation is involved).

Raises SaleDocumentError (bad input, `.field` names the input at fault), SaleDocumentStateError (wrong state,
a SaleStateError so the existing 409 handling applies) and django PermissionDenied (another company).
"""
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from .document_services import current_work_type
from .models import DocumentType, Obligation, ObligationEvent, Sale, SaleDocument, SaleEvent
from .services import SaleStateError, _checked_date, _clean_text, _log_sale, update_obligation

MIN_REASON = 3
REASON_LIMIT = 1000
REFERENCE_LIMIT = 120


class SaleDocumentError(ValueError):
    """The request breaks a rule. `field` names the input at fault so a form can show the message beside it."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


class SaleDocumentStateError(SaleStateError):
    """The sale or document is in the wrong state for this action (a cancelled sale, an issued document...)."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


# ---------- input checks ----------

def _reason(value, label):
    text = (value or "").strip()
    if len(text) < MIN_REASON:
        raise SaleDocumentError(f"{label} is required (at least {MIN_REASON} characters).", "reason")
    if len(text) > REASON_LIMIT:
        raise SaleDocumentError(f"{label} is too long (maximum {REASON_LIMIT} characters).", "reason")
    return text


def _reference(value):
    text = _clean_text(value)
    if len(text) > REFERENCE_LIMIT:
        raise SaleDocumentError(f"Reference is too long (maximum {REFERENCE_LIMIT} characters).", "reference")
    return text


def _issue_date(value):
    if value is None or value == "":
        raise SaleDocumentError("The issue date is required.", "issued_on")
    try:
        return _checked_date(value, "Issue date")
    except ValueError as exc:
        raise SaleDocumentError(str(exc), "issued_on") from None


# ---------- locking and relationship checks ----------

def _lock_sale(sale_id, actor):
    """Lock the sale, then refuse another company's actor or a cancelled sale."""
    sale = Sale.objects.select_for_update().get(pk=sale_id)
    if actor.company_id != sale.company_id:
        raise PermissionDenied("Sale belongs to another company.")
    if sale.status != Sale.Status.ACTIVE:
        raise SaleDocumentStateError("This sale is cancelled, so its documents cannot be changed.")
    return sale


def _lock_document(document, actor):
    """Lock sale, document and obligation (in that order) and prove they belong together.
    Returns (sale, document, obligation or None)."""
    sale = _lock_sale(document.sale_id, actor)
    doc = SaleDocument.objects.select_for_update().select_related("document_type").get(pk=document.pk)
    if doc.company_id != sale.company_id or doc.document_type.company_id != sale.company_id:
        raise PermissionDenied("Document belongs to another company.")
    obligation = None
    if doc.obligation_id is not None:
        obligation = Obligation.objects.select_for_update().select_related("obligation_type").get(pk=doc.obligation_id)
        if obligation.company_id != sale.company_id:
            raise PermissionDenied("Obligation belongs to another company.")
        if (obligation.sale_id != sale.pk
                or obligation.obligation_type.catalogue_entry_id != doc.document_type_id):
            raise SaleDocumentStateError(
                "This document is linked to an obligation that does not belong to it, so it cannot be changed. "
                "Nothing was changed.")
    return sale, doc, obligation


def _iso(value):
    return value.isoformat() if value else None


# ---------- writes ----------

@transaction.atomic
def add_document(sale, actor, document_type, state, issued_on=None, reference=""):
    """Add one catalogue document to a sale, as OWED or already ISSUED (see the module notes)."""
    sale = _lock_sale(sale.pk, actor)
    entry = DocumentType.objects.select_for_update().get(pk=document_type.pk)
    if entry.company_id != sale.company_id:
        raise PermissionDenied("Document type belongs to another company.")
    if state not in (SaleDocument.State.OWED, SaleDocument.State.ISSUED):
        raise SaleDocumentError("A document can only be added as owed or issued.", "state")
    if state == SaleDocument.State.ISSUED:
        day = _issue_date(issued_on)
        reference = _reference(reference)
    elif issued_on not in (None, "") or _clean_text(reference):
        raise SaleDocumentError("An issue date and reference can only be given for an issued document.", "issued_on")
    if not entry.is_active:
        raise SaleDocumentStateError("This document type is inactive, so it cannot be added to a sale.",
                                     "document_type")
    if SaleDocument.objects.filter(sale=sale, document_type=entry).exists():
        raise SaleDocumentStateError("This sale already has this document.", "document_type")

    existing = list(Obligation.objects.select_for_update().filter(
        sale=sale, obligation_type__catalogue_entry=entry).select_related("obligation_type").order_by("id"))
    if len(existing) > 1:
        raise SaleDocumentStateError(
            "This sale has more than one obligation for this document, so it cannot be added automatically.")
    now = timezone.now()
    base = dict(company=sale.company, sale=sale, document_type=entry, name_snapshot=entry.name, added_by=actor)
    changes = {"document": [None, entry.name], "state": [None, state]}

    try:
        with transaction.atomic():
            if state == SaleDocument.State.ISSUED:
                if existing:
                    raise SaleDocumentStateError(
                        "This sale already has an obligation for this document. Add it as owed first, then record "
                        "its issuance.", "state")
                document = SaleDocument.objects.create(
                    state=SaleDocument.State.ISSUED, issued_on=day, reference=reference, issued_by=actor,
                    issued_at=now, **base)
                changes["issued_on"] = [None, _iso(day)]
                if reference:
                    changes["reference"] = [None, reference]
                note = "Added as already issued"
            else:
                if existing:
                    obligation = existing[0]
                    if sale.documents_managed:
                        raise SaleDocumentStateError(
                            "A document-managed sale should not already have an obligation for this document, so "
                            "it was not added.", "document_type")
                    if obligation.status not in Obligation.OPEN_STATUSES:
                        raise SaleDocumentStateError(
                            "The sale's existing obligation for this document is already "
                            + ("completed" if obligation.status == Obligation.Status.COMPLETED
                               else "marked not applicable")
                            + ". It may already stand for a document that was issued or delivered, so it has not "
                            "been linked or reopened automatically. Review that obligation first.", "document_type")
                    ObligationEvent.objects.create(
                        company=sale.company, obligation=obligation, actor=actor,
                        note=f"Linked to the sale's document '{entry.name}' (existing obligation adopted)")
                    note = "Added as owed; the existing obligation was adopted"
                else:
                    work = current_work_type(entry)
                    if work is None:
                        raise SaleDocumentStateError(
                            "This document type has no current work type, so it cannot be added.", "document_type")
                    today = timezone.localdate()
                    obligation = Obligation.objects.create(
                        company=sale.company, sale=sale, obligation_type=work, started_on=today,
                        due_date=today + timedelta(days=work.sla_days), department=work.default_department)
                    ObligationEvent.objects.create(
                        company=sale.company, obligation=obligation, actor=actor,
                        note=f"Created for the sale's document '{entry.name}'")
                    note = "Added as owed; an obligation was created"
                document = SaleDocument.objects.create(state=SaleDocument.State.OWED, obligation=obligation, **base)
                changes["obligation"] = [None, obligation.pk]
    except IntegrityError:
        raise SaleDocumentStateError("This sale already has this document.", "document_type") from None
    _log_sale(sale, actor, SaleEvent.Action.DOCUMENT_ADDED, changes, f"{entry.name}: {note}")
    return document


@transaction.atomic
def issue_document(document, actor, issued_on, reference=""):
    """Record that an OWED document has actually been issued to the buyer. Needs an issue date."""
    day = _issue_date(issued_on)
    reference = _reference(reference)
    sale, doc, obligation = _lock_document(document, actor)
    if doc.state == SaleDocument.State.ISSUED:
        raise SaleDocumentStateError("This document has already been issued.")
    if doc.state == SaleDocument.State.NOT_APPLICABLE:
        raise SaleDocumentStateError("This document was marked not applicable, so it cannot be issued.")
    if obligation is not None and obligation.status == Obligation.Status.NOT_APPLICABLE:
        raise SaleDocumentStateError(
            "The obligation for this document is marked not applicable. Reopen it before issuing the document.")
    doc.state = SaleDocument.State.ISSUED
    doc.issued_on, doc.reference = day, reference
    doc.issued_by, doc.issued_at = actor, timezone.now()
    doc.save(update_fields=["state", "issued_on", "reference", "issued_by", "issued_at"])
    if obligation is not None:
        note = f"Document issued on {_iso(day)}" + (f" (reference {reference})" if reference else "")
        if obligation.status in Obligation.OPEN_STATUSES:
            update_obligation(obligation, actor, note=note, status=Obligation.Status.COMPLETED)
        else:
            update_obligation(obligation, actor, note=note)         # already completed: a history entry only
    _log_sale(sale, actor, SaleEvent.Action.DOCUMENT_ISSUED,
              {"state": [SaleDocument.State.OWED, SaleDocument.State.ISSUED], "issued_on": [None, _iso(day)],
               "reference": ["", reference]}, doc.name_snapshot)
    return doc


@transaction.atomic
def mark_not_applicable(document, actor, reason):
    """Record that an OWED document turned out not to be required. Needs a reason."""
    reason = _reason(reason, "A reason")
    sale, doc, obligation = _lock_document(document, actor)
    if doc.state == SaleDocument.State.ISSUED:
        raise SaleDocumentStateError("An issued document cannot be marked not applicable.")
    if doc.state == SaleDocument.State.NOT_APPLICABLE:
        raise SaleDocumentStateError("This document is already marked not applicable.")
    doc.state = SaleDocument.State.NOT_APPLICABLE
    doc.not_applicable_reason = reason
    doc.not_applicable_by, doc.not_applicable_at = actor, timezone.now()
    doc.save(update_fields=["state", "not_applicable_reason", "not_applicable_by", "not_applicable_at"])
    if obligation is not None:
        note = f"Document marked not applicable: {reason}"
        if obligation.status in Obligation.OPEN_STATUSES:
            update_obligation(obligation, actor, note=note, status=Obligation.Status.NOT_APPLICABLE)
        else:
            update_obligation(obligation, actor, note=note)         # completed (or already closed): left as it is
    _log_sale(sale, actor, SaleEvent.Action.DOCUMENT_NOT_APPLICABLE,
              {"state": [SaleDocument.State.OWED, SaleDocument.State.NOT_APPLICABLE]},
              f"{doc.name_snapshot}: {reason}")
    return doc


@transaction.atomic
def correct_issuance(document, actor, reason, issued_on=None, reference=None):
    """Correct the issue date and/or reference of an ISSUED document. Needs a reason and a real change. The
    original issuer and timestamp stay; the event records who corrected it and the before/after values."""
    reason = _reason(reason, "A reason for the correction")
    if issued_on in (None, "") and reference is None:
        raise SaleDocumentError("Nothing to change.")
    new_day = None if issued_on in (None, "") else _issue_date(issued_on)
    new_reference = None if reference is None else _reference(reference)
    sale, doc, obligation = _lock_document(document, actor)
    if doc.state != SaleDocument.State.ISSUED:
        raise SaleDocumentStateError("Only an issued document can have its issuance corrected.")
    changes, fields = {}, []
    if new_day is not None and new_day != doc.issued_on:
        changes["issued_on"] = [_iso(doc.issued_on), _iso(new_day)]
        doc.issued_on = new_day
        fields.append("issued_on")
    if new_reference is not None and new_reference != doc.reference:
        changes["reference"] = [doc.reference, new_reference]
        doc.reference = new_reference
        fields.append("reference")
    if not changes:
        raise SaleDocumentError("Nothing to change.")
    doc.save(update_fields=fields)
    if obligation is not None:
        update_obligation(obligation, actor, note=f"Issuance corrected: {reason}")
    _log_sale(sale, actor, SaleEvent.Action.DOCUMENT_ISSUANCE_CORRECTED, changes, f"{doc.name_snapshot}: {reason}")
    return doc

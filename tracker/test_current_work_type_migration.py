import importlib
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.db import IntegrityError, transaction

from .models import DocumentType, ObligationType
from .test_cancel_and_reports import Base

mark_current = importlib.import_module("tracker.migrations.0011_obligationtype_is_current").mark_current_work_types

FIELDS = ("id", "company_id", "name", "trigger", "sla_days", "default_department_id", "is_active", "auto_generate",
          "catalogue_entry_id")


def snapshot():
    return list(ObligationType.objects.order_by("id").values_list(*FIELDS))


class CurrentWorkTypeConstraintTests(Base):
    def entry(self, name):
        return DocumentType.objects.create(company=self.co, name=name)

    def work(self, name, entry=None, **extra):
        return ObligationType.objects.create(company=self.co, name=name, catalogue_entry=entry, **extra)

    def test_only_one_work_type_per_entry_can_be_current(self):
        entry = self.entry("A")
        first = self.work("A1", entry, is_current=True)
        second = self.work("A2", entry)                                    # not current: fine
        with self.assertRaises(IntegrityError), transaction.atomic():
            ObligationType.objects.filter(pk=second.pk).update(is_current=True)
        ObligationType.objects.filter(pk=first.pk).update(is_current=False)        # hand over in the right order
        ObligationType.objects.filter(pk=second.pk).update(is_current=True)
        self.assertEqual(ObligationType.objects.filter(catalogue_entry=entry, is_current=True).get(), second)

    def test_each_entry_has_its_own_current_work_type_and_any_number_of_earlier_ones(self):
        a, b = self.entry("A"), self.entry("B")
        self.work("A1", a, is_current=True)
        self.work("B1", b, is_current=True)
        for i in range(3):
            self.work(f"A-old-{i}", a)
        self.assertEqual(ObligationType.objects.filter(is_current=True).count(), 2)

    def test_types_without_an_entry_are_not_constrained_and_cannot_be_current(self):
        self.work("Loose 1")
        self.work("Loose 2")                                               # several NULL entries: fine
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.work("Loose 3", is_current=True)                          # current but no entry: refused

    def test_the_model_changes_are_fully_covered_by_migrations(self):
        call_command("makemigrations", "--check", "--dry-run", stdout=StringIO(), stderr=StringIO())


class MarkCurrentDataStepTests(Base):
    """The real 0011 function against live models, in the state 1B leaves behind: entries linked to their
    existing types, nothing marked current yet."""

    def setUp(self):
        super().setUp()
        self.old = ObligationType.objects.create(
            company=self.co, name="Old Doc", trigger="on_full_payment", sla_days=15, is_active=False)
        self.catalogue_only = ObligationType.objects.create(
            company=self.other, name="Receipt", trigger="on_sale", sla_days=7, auto_generate=False)
        self.loose = ObligationType.objects.create(company=self.other, name="Loose")
        self.entries = {}
        for t in (self.alloc, self.deed, self.old, self.catalogue_only):
            entry = DocumentType.objects.create(company=t.company, name=t.name, is_active=t.is_active)
            ObligationType.objects.filter(pk=t.pk).update(catalogue_entry=entry)
            self.entries[t.pk] = entry

    def run_step(self):
        mark_current(apps, None)

    def test_every_linked_type_becomes_current_and_nothing_else_changes(self):
        before = snapshot()
        self.run_step()
        self.assertEqual(snapshot(), before)
        linked = ObligationType.objects.filter(catalogue_entry__isnull=False)
        self.assertEqual(linked.count(), 4)
        self.assertTrue(all(t.is_current for t in linked))
        self.assertEqual(ObligationType.objects.filter(is_current=True).count(), 4)

    def test_unlinked_types_stay_not_current(self):
        self.run_step()
        self.assertFalse(ObligationType.objects.get(pk=self.loose.pk).is_current)

    def test_inactive_and_catalogue_only_types_keep_their_settings(self):
        self.run_step()
        old = ObligationType.objects.get(pk=self.old.pk)
        receipt = ObligationType.objects.get(pk=self.catalogue_only.pk)
        self.assertEqual((old.is_active, old.trigger, old.sla_days, old.auto_generate), (False, "on_full_payment", 15, True))
        self.assertEqual((receipt.auto_generate, receipt.trigger, receipt.sla_days), (False, "on_sale", 7))
        self.assertEqual(ObligationType.objects.get(pk=self.deed.pk).default_department_id, self.legal.pk)

    def test_running_it_twice_changes_nothing_more(self):
        self.run_step()
        once = snapshot(), sorted(ObligationType.objects.filter(is_current=True).values_list("pk", flat=True))
        self.run_step()
        self.assertEqual((snapshot(), sorted(ObligationType.objects.filter(is_current=True).values_list("pk", flat=True))), once)

    def test_an_entry_with_two_work_types_aborts_and_changes_nothing(self):
        extra = ObligationType.objects.create(
            company=self.co, name="Deed v2", catalogue_entry=self.entries[self.deed.pk])
        before = snapshot()
        with self.assertRaises(RuntimeError) as caught:
            self.run_step()
        self.assertIn("Deed", str(caught.exception))
        self.assertIn(str(extra.pk), str(caught.exception))
        self.assertEqual(snapshot(), before)
        self.assertFalse(ObligationType.objects.filter(is_current=True).exists())

    def test_nothing_to_do_is_fine(self):
        ObligationType.objects.update(catalogue_entry=None)
        self.run_step()
        self.assertFalse(ObligationType.objects.filter(is_current=True).exists())

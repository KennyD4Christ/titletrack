from django.core.exceptions import PermissionDenied

from . import document_services as catalogue
from .models import (
    Department, DocumentType, DocumentTypeEvent, Obligation, ObligationType, SaleDocument,
)
from .test_cancel_and_reports import Base


class CatalogueBase(Base):
    def entry(self, name="Survey Plan", **extra):
        return catalogue.create_document_type(self.co, self.manager, name=name, **extra)

    def work(self, entry):
        return catalogue.current_work_type(entry)

    def use(self, entry):
        """Give the entry's current work type an obligation, which makes it 'used'."""
        sale = self.sale(price=1000, paid=400)
        return Obligation.objects.create(
            company=self.co, sale=sale, obligation_type=self.work(entry), started_on=self.today,
            due_date=self.today)

    def legacy_entry(self, obligation_type):
        """What the 1B backfill does: an entry paired with an existing automatic type, which is its current one."""
        entry = DocumentType.objects.create(company=self.co, name=obligation_type.name)
        ObligationType.objects.filter(pk=obligation_type.pk).update(catalogue_entry=entry, is_current=True)
        return entry

    def actions(self, entry):
        return list(entry.events.order_by("id").values_list("action", flat=True))

    def one_current(self, entry):
        self.assertEqual(ObligationType.objects.filter(catalogue_entry=entry, is_current=True).count(), 1)

    def counts(self):
        return (DocumentType.objects.count(), ObligationType.objects.count(), DocumentTypeEvent.objects.count())


class CreateTests(CatalogueBase):
    def test_creates_the_entry_its_current_work_type_and_one_event(self):
        entry = self.entry()
        work = self.work(entry)
        self.assertEqual((entry.company_id, entry.name, entry.is_active, entry.sort_order, entry.category),
                         (self.co.pk, "Survey Plan", True, 0, ""))
        self.assertEqual((work.name, work.company_id, work.trigger, work.sla_days, work.default_department_id),
                         ("Survey Plan", self.co.pk, "on_sale", 90, None))
        self.assertEqual((work.is_active, work.auto_generate, work.is_current), (True, False, True))
        self.assertEqual(self.actions(entry), ["created"])
        event = entry.events.get()
        self.assertEqual((event.actor, event.company_id), (self.manager, self.co.pk))
        self.assertEqual(event.changes["name"], [None, "Survey Plan"])
        self.assertEqual(event.changes["work_type"], [None, work.pk])

    def test_details_sla_and_department_are_stored(self):
        entry = self.entry(description="Surveyed plan", category="Survey", sort_order=5, sla_days=45,
                           default_department=self.legal)
        work = self.work(entry)
        self.assertEqual((entry.description, entry.category, entry.sort_order), ("Surveyed plan", "Survey", 5))
        self.assertEqual((work.sla_days, work.default_department_id), (45, self.legal.pk))

    def test_text_is_cleaned(self):
        self.assertEqual(self.entry("  Survey    Plan ", category="  Land   survey ").name, "Survey Plan")
        self.assertEqual(DocumentType.objects.get().category, "Land survey")

    def test_bad_input_is_refused_and_nothing_is_written(self):
        foreign_department = Department.objects.create(company=self.other, name="Elsewhere")
        cases = {
            "name": [dict(name=""), dict(name="   "), dict(name="x" * 121)],
            "category": [dict(name="A", category="c" * 61)],
            "description": [dict(name="A", description="d" * 1001)],
            "sla_days": [dict(name="A", sla_days=0), dict(name="A", sla_days=3651), dict(name="A", sla_days=True),
                         dict(name="A", sla_days="30")],
            "sort_order": [dict(name="A", sort_order=-1), dict(name="A", sort_order=1_000_001)],
            "default_department": [dict(name="A", default_department=foreign_department)],
        }
        before = self.counts()
        for field, variants in cases.items():
            for kwargs in variants:
                with self.assertRaises(catalogue.CatalogueError, msg=kwargs) as caught:
                    catalogue.create_document_type(self.co, self.manager, **kwargs)
                self.assertEqual(caught.exception.field, field, kwargs)
        self.assertEqual(self.counts(), before)

    def test_names_are_unique_per_company_ignoring_case(self):
        self.entry("Survey Plan")
        before = self.counts()
        with self.assertRaises(catalogue.CatalogueError) as caught:
            self.entry("survey PLAN")
        self.assertEqual(caught.exception.field, "name")
        self.assertEqual(self.counts(), before)
        other = catalogue.create_document_type(self.other, self.eve, name="Survey Plan")      # another company may
        self.assertEqual(other.company_id, self.other.pk)

    def test_a_name_used_by_an_unlinked_work_type_is_refused(self):
        with self.assertRaises(catalogue.CatalogueError) as caught:
            self.entry("allocation letter")                       # Allocation Letter exists in the settings
        self.assertEqual(caught.exception.field, "name")

    def test_another_companys_actor_is_refused(self):
        before = self.counts()
        with self.assertRaises(PermissionDenied):
            catalogue.create_document_type(self.co, self.eve, name="Survey Plan")
        self.assertEqual(self.counts(), before)

    def test_a_new_entry_never_joins_legacy_automatic_generation(self):
        self.entry("Survey Plan")
        sale = self.sale(price=1000, paid=1000)
        self.assertEqual(set(sale.obligations.values_list("obligation_type__name", flat=True)),
                         {"Allocation Letter", "Deed"})
        self.assertEqual(ObligationType.objects.get(name="Survey Plan").auto_generate, False)


class UpdateTests(CatalogueBase):
    def test_details_are_changed_with_before_and_after_in_the_history(self):
        entry = self.entry(description="old", category="Land")
        catalogue.update_document_type(entry, self.manager, description="new", category="Survey", sort_order=3)
        entry.refresh_from_db()
        self.assertEqual((entry.description, entry.category, entry.sort_order), ("new", "Survey", 3))
        event = entry.events.latest("id")
        self.assertEqual(event.action, "edited")
        self.assertEqual(event.changes, {"description": ["old", "new"], "category": ["Land", "Survey"],
                                         "sort_order": [0, 3]})
        self.assertEqual(event.actor, self.manager)

    def test_sla_and_department_change_the_current_work_type_and_not_existing_obligations(self):
        entry = self.entry(sla_days=60)
        obligation = self.use(entry)
        due_before = obligation.due_date
        catalogue.update_document_type(entry, self.manager, sla_days=10, default_department=self.legal)
        work = self.work(entry)
        self.assertEqual((work.sla_days, work.default_department_id), (10, self.legal.pk))
        obligation.refresh_from_db()
        self.assertEqual(obligation.due_date, due_before)
        self.assertEqual(entry.events.latest("id").changes,
                         {"sla_days": [60, 10], "default_department": [None, self.legal.pk]})

    def test_the_department_can_be_cleared(self):
        entry = self.entry(default_department=self.legal)
        catalogue.update_document_type(entry, self.manager, default_department=None)
        self.assertIsNone(self.work(entry).default_department_id)

    def test_nothing_to_change_is_refused(self):
        entry = self.entry(description="same", sla_days=60)
        events = entry.events.count()
        for kwargs in ({}, {"description": "same"}, {"sla_days": 60}, {"default_department": None}):
            with self.assertRaises(catalogue.CatalogueError, msg=kwargs):
                catalogue.update_document_type(entry, self.manager, **kwargs)
        self.assertEqual(entry.events.count(), events)

    def test_bad_values_and_foreign_departments_are_refused_without_a_partial_write(self):
        entry = self.entry(description="keep", sla_days=60)
        foreign = Department.objects.create(company=self.other, name="Elsewhere")
        for kwargs in (dict(description="new", sla_days=0), dict(description="new", default_department=foreign),
                       dict(category="c" * 61)):
            with self.assertRaises(catalogue.CatalogueError, msg=kwargs):
                catalogue.update_document_type(entry, self.manager, **kwargs)
        entry.refresh_from_db()
        self.assertEqual((entry.description, self.work(entry).sla_days, entry.events.count()), ("keep", 60, 1))

    def test_another_companys_actor_is_refused(self):
        entry = self.entry()
        with self.assertRaises(PermissionDenied):
            catalogue.update_document_type(entry, self.eve, description="hijack")
        entry.refresh_from_db()
        self.assertEqual(entry.description, "")


class RenameTests(CatalogueBase):
    def test_an_unused_entry_is_renamed_together_with_its_work_type(self):
        entry = self.entry("Survey Plan")
        work = self.work(entry)
        catalogue.rename_document_type(entry, self.manager, "Survey Plan v2")
        entry.refresh_from_db()
        work.refresh_from_db()
        self.assertEqual((entry.name, work.name, work.is_current), ("Survey Plan v2", "Survey Plan v2", True))
        self.assertEqual(ObligationType.objects.filter(catalogue_entry=entry).count(), 1)
        event = entry.events.latest("id")
        self.assertEqual((event.action, event.changes["name"]), ("renamed", ["Survey Plan", "Survey Plan v2"]))
        self.assertEqual(event.changes["work_type"], [work.pk, work.pk])

    def test_a_used_entry_gets_a_replacement_work_type_and_history_keeps_its_name(self):
        entry = self.entry("Survey Plan", sla_days=45, default_department=self.legal)
        obligation = self.use(entry)
        old = self.work(entry)
        snapshot = SaleDocument.objects.create(
            company=self.co, sale=obligation.sale, document_type=entry, name_snapshot="Survey Plan",
            state=SaleDocument.State.OWED, obligation=obligation)
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        entry.refresh_from_db()
        old.refresh_from_db()
        new = self.work(entry)
        self.assertEqual(entry.name, "Land Survey")
        self.assertNotEqual(new.pk, old.pk)
        self.assertEqual((old.name, old.is_current, old.auto_generate), ("Survey Plan", False, False))
        self.assertEqual((new.name, new.is_current, new.auto_generate, new.is_active), ("Land Survey", True, False, True))
        self.assertEqual((new.trigger, new.sla_days, new.default_department_id), ("on_sale", 45, self.legal.pk))
        obligation.refresh_from_db()
        self.assertEqual((obligation.obligation_type_id, obligation.obligation_type.name), (old.pk, "Survey Plan"))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.name_snapshot, "Survey Plan")
        self.one_current(entry)
        self.assertIn("replacement", entry.events.latest("id").note)

    def test_renaming_a_legacy_entry_leaves_the_automatic_type_and_legacy_sales_alone(self):
        entry = self.legacy_entry(self.deed)
        self.sale()                                              # the Deed type now has an obligation: it is used
        catalogue.rename_document_type(entry, self.manager, "Deed of Assignment")
        self.deed.refresh_from_db()
        self.assertEqual((self.deed.name, self.deed.is_active, self.deed.auto_generate, self.deed.trigger,
                          self.deed.sla_days, self.deed.default_department_id, self.deed.is_current),
                         ("Deed", True, True, "on_full_payment", 90, self.legal.pk, False))
        new = self.work(entry)
        self.assertEqual((new.name, new.auto_generate, new.trigger), ("Deed of Assignment", False, "on_full_payment"))
        later = self.sale()                                      # a legacy sale made after the rename
        self.assertEqual(set(later.obligations.values_list("obligation_type__name", flat=True)),
                         {"Allocation Letter", "Deed"})
        self.assertFalse(Obligation.objects.filter(obligation_type=new).exists())
        self.one_current(entry)

    def test_renaming_back_makes_the_earlier_work_type_current_again(self):
        entry = self.entry("Survey Plan")
        self.use(entry)
        first = self.work(entry)
        catalogue.rename_document_type(entry, self.manager, "Land Survey")
        second = self.work(entry)
        catalogue.rename_document_type(entry, self.manager, "Survey Plan")
        entry.refresh_from_db()
        self.assertEqual(entry.name, "Survey Plan")
        self.assertEqual(self.work(entry).pk, first.pk)
        self.assertEqual(ObligationType.objects.filter(catalogue_entry=entry).count(), 2)       # no third row
        self.assertFalse(ObligationType.objects.get(pk=second.pk).is_current)
        self.one_current(entry)
        self.assertIn("current again", entry.events.latest("id").note)

    def test_a_case_only_rename(self):
        entry = self.entry("Survey Plan")
        catalogue.rename_document_type(entry, self.manager, "survey plan")
        entry.refresh_from_db()
        self.assertEqual((entry.name, self.work(entry).name), ("survey plan", "survey plan"))

    def test_names_that_are_taken_or_unchanged_are_refused(self):
        entry, other = self.entry("Survey Plan"), self.entry("Title Registration")
        before = self.counts()
        for name in ("Survey Plan", "title registration", "allocation LETTER", "", "x" * 121):
            with self.assertRaises(catalogue.CatalogueError, msg=name) as caught:
                catalogue.rename_document_type(entry, self.manager, name)
            self.assertEqual(caught.exception.field, "name", name)
        self.assertEqual(self.counts(), before)
        entry.refresh_from_db()
        self.assertEqual((entry.name, self.work(entry).name), ("Survey Plan", "Survey Plan"))
        self.assertEqual(other.name, "Title Registration")

    def test_another_companys_actor_is_refused(self):
        entry = self.entry()
        with self.assertRaises(PermissionDenied):
            catalogue.rename_document_type(entry, self.eve, "Hijacked")
        entry.refresh_from_db()
        self.assertEqual(entry.name, "Survey Plan")

    def test_an_entry_without_a_current_work_type_cannot_be_renamed(self):
        entry = self.entry()
        ObligationType.objects.filter(catalogue_entry=entry).update(is_current=False)
        with self.assertRaises(catalogue.CatalogueStateError):
            catalogue.rename_document_type(entry, self.manager, "Land Survey")

    def test_there_is_always_exactly_one_current_work_type(self):
        entry = self.entry("A")
        for name in ("B", "C", "A", "D", "B"):
            if not Obligation.objects.filter(obligation_type=self.work(entry)).exists():
                self.use(entry)                                  # make the current work type 'used' first
            catalogue.rename_document_type(entry, self.manager, name)
            self.one_current(entry)
        entry.refresh_from_db()
        self.assertEqual((entry.name, self.work(entry).name), ("B", "B"))


class ActivationTests(CatalogueBase):
    def test_deactivating_and_reactivating_are_audited(self):
        entry = self.entry()
        catalogue.set_document_type_active(entry, self.manager, False)
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)
        catalogue.set_document_type_active(entry, self.manager, True)
        entry.refresh_from_db()
        self.assertTrue(entry.is_active)
        self.assertEqual(self.actions(entry), ["created", "deactivated", "activated"])
        self.assertEqual(entry.events.get(action="deactivated").changes, {"is_active": [True, False]})

    def test_doing_it_twice_is_a_state_error(self):
        entry = self.entry()
        with self.assertRaises(catalogue.CatalogueStateError):
            catalogue.set_document_type_active(entry, self.manager, True)
        catalogue.set_document_type_active(entry, self.manager, False)
        with self.assertRaises(catalogue.CatalogueStateError):
            catalogue.set_document_type_active(entry, self.manager, False)

    def test_deactivation_never_touches_the_work_types_or_legacy_generation(self):
        entry = self.legacy_entry(self.deed)
        catalogue.set_document_type_active(entry, self.manager, False)
        self.deed.refresh_from_db()
        self.assertEqual((self.deed.is_active, self.deed.auto_generate, self.deed.is_current), (True, True, True))
        sale = self.sale(price=1000, paid=1000)                  # legacy generation carries on
        self.assertEqual(set(sale.obligations.values_list("obligation_type__name", flat=True)),
                         {"Allocation Letter", "Deed"})

    def test_a_deactivated_catalogue_only_entry_keeps_its_active_work_type(self):
        entry = self.entry()
        catalogue.set_document_type_active(entry, self.manager, False)
        self.assertTrue(self.work(entry).is_active)

    def test_another_companys_actor_is_refused(self):
        entry = self.entry()
        with self.assertRaises(PermissionDenied):
            catalogue.set_document_type_active(entry, self.eve, False)
        entry.refresh_from_db()
        self.assertTrue(entry.is_active)


class SelectionTests(CatalogueBase):
    def test_the_current_work_type_is_the_flagged_one(self):
        entry = self.entry()
        self.assertEqual(catalogue.current_work_type(entry).name, "Survey Plan")
        ObligationType.objects.filter(catalogue_entry=entry).update(is_current=False)
        self.assertIsNone(catalogue.current_work_type(entry))

    def test_work_types_lists_every_work_type_oldest_first(self):
        entry = self.entry("A")
        self.use(entry)
        catalogue.rename_document_type(entry, self.manager, "B")
        self.assertEqual([t.name for t in catalogue.work_types(entry)], ["A", "B"])

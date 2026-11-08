from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from api.permissions import MANAGER, MATRIX, MD
from tracker import document_services as catalogue
from tracker import services
from tracker.models import (
    Buyer, Company, Department, DocumentType, DocumentTypeEvent, Estate, Obligation, ObligationType, Plot, User,
)

ROW_KEYS = {"id", "name", "description", "category", "is_active", "sort_order", "created_at", "work_type",
            "sla_days", "default_department", "drives_legacy_generation"}


class CatalogueApiBase(APITestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.legal = Department.objects.create(company=self.a, name="Legal")
        self.b_legal = Department.objects.create(company=self.b, name="Legal")
        make = lambda name, role, company=self.a: User.objects.create_user(name, password="x", company=company, role=role)
        self.md, self.manager = make("md", "md"), make("manager", "manager")
        self.staff, self.sales, self.viewer = make("staff", "staff"), make("sales", "sales"), make("viewer", "viewer")
        self.b_manager = make("b_manager", "manager", self.b)
        self.outsider = User.objects.create_user("outsider", password="x", role="staff")        # no company
        self.survey = catalogue.create_document_type(
            self.a, self.md, name="Survey Plan", sla_days=60, default_department=self.legal)
        self.b_entry = catalogue.create_document_type(self.b, self.b_manager, name="Deed")

    def call(self, user, method, url, payload=None):
        self.client.force_authenticate(user)
        return getattr(self.client, method)(url, payload or {}, format="json")

    def list_url(self):
        return reverse("document-type-list")

    def detail(self, pk):
        return reverse("document-type-detail", args=[pk])

    def action(self, name, pk):
        return reverse(f"document-type-{name}", args=[pk])

    def names(self, user, **params):
        self.client.force_authenticate(user)
        response = self.client.get(self.list_url(), {"page_size": 200, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return [row["name"] for row in response.data["results"]]

    def used_sale_for(self, entry):
        estate = Estate.objects.create(company=entry.company, name="Green Valley")
        plot = Plot.objects.create(company=entry.company, estate=estate, plot_number="P-1")
        buyer = Buyer.objects.create(company=entry.company, full_name="Buyer")
        sale = services.create_sale(entry.company, plot, buyer, 1000, self.today, 400, self.today)
        return Obligation.objects.create(
            company=entry.company, sale=sale, obligation_type=catalogue.current_work_type(entry),
            started_on=self.today, due_date=self.today)


class CatalogueReadTests(CatalogueApiBase):
    def test_everyone_in_a_company_reads_only_their_own_catalogue(self):
        for user in (self.md, self.manager, self.staff, self.sales, self.viewer):
            self.assertEqual(self.names(user), ["Survey Plan"], user.role)
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.detail(self.survey.pk)).status_code, 200, user.role)
        self.assertEqual(self.names(self.b_manager), ["Deed"])

    def test_people_without_a_company_or_a_login_are_refused(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.list_url()).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.list_url()).status_code, (401, 403))

    def test_the_response_has_exactly_the_documented_fields(self):
        self.client.force_authenticate(self.manager)
        row = self.client.get(self.detail(self.survey.pk)).data
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual((row["name"], row["sla_days"], row["default_department"], row["work_type"],
                          row["drives_legacy_generation"], row["is_active"]),
                         ("Survey Plan", 60, self.legal.pk, catalogue.current_work_type(self.survey).pk, False, True))

    def test_an_entry_that_came_from_an_automatic_type_says_so(self):
        legacy_type = ObligationType.objects.create(company=self.a, name="Deed of Assignment", sla_days=90)
        entry = DocumentType.objects.create(company=self.a, name="Deed of Assignment")
        ObligationType.objects.filter(pk=legacy_type.pk).update(catalogue_entry=entry, is_current=True)
        self.client.force_authenticate(self.manager)
        self.assertTrue(self.client.get(self.detail(entry.pk)).data["drives_legacy_generation"])

    def test_filter_by_active_state_and_ordering(self):
        catalogue.create_document_type(self.a, self.md, name="Zeta", sort_order=1)
        catalogue.create_document_type(self.a, self.md, name="Alpha", sort_order=1)
        catalogue.create_document_type(self.a, self.md, name="First", sort_order=0)
        catalogue.set_document_type_active(DocumentType.objects.get(name="Zeta"), self.md, False)
        self.assertEqual(self.names(self.manager), ["First", "Survey Plan", "Alpha", "Zeta"])     # sort_order, then name
        self.assertEqual(self.names(self.manager, is_active="true"), ["First", "Survey Plan", "Alpha"])
        self.assertEqual(self.names(self.manager, is_active="false"), ["Zeta"])

    def test_listing_costs_a_fixed_number_of_queries(self):
        self.client.force_authenticate(self.manager)
        with CaptureQueriesContext(connection) as small:
            self.client.get(self.list_url(), {"page_size": 200})
        for i in range(10):
            catalogue.create_document_type(self.a, self.md, name=f"Extra {i}")
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(self.list_url(), {"page_size": 200})
        self.assertEqual(response.data["count"], 11)
        self.assertEqual(len(small), len(large))


class CataloguePermissionTests(CatalogueApiBase):
    def test_the_catalogue_action_is_md_and_manager_only(self):
        self.assertEqual(MATRIX["manage_document_catalogue"], {MD, MANAGER})

    def test_other_roles_cannot_write_and_nothing_changes(self):
        before = (DocumentType.objects.count(), ObligationType.objects.count(), DocumentTypeEvent.objects.count())
        pk = self.survey.pk
        for user in (self.staff, self.sales, self.viewer, self.outsider):
            for method, url, payload in (
                ("post", self.list_url(), {"name": "Nope"}), ("patch", self.detail(pk), {"description": "x"}),
                ("post", self.action("rename", pk), {"name": "Nope"}), ("post", self.action("activate", pk), {}),
                ("post", self.action("deactivate", pk), {}),
            ):
                self.assertEqual(self.call(user, method, url, payload).status_code, 403, f"{user.role} {method} {url}")
        self.assertEqual((DocumentType.objects.count(), ObligationType.objects.count(),
                          DocumentTypeEvent.objects.count()), before)

    def test_md_and_manager_can_write(self):
        for user, name in ((self.md, "From MD"), (self.manager, "From Manager")):
            created = self.call(user, "post", self.list_url(), {"name": name})
            self.assertEqual(created.status_code, 201, created.data)
            pk = created.data["id"]
            self.assertEqual(self.call(user, "patch", self.detail(pk), {"description": "d"}).status_code, 200)
            self.assertEqual(self.call(user, "post", self.action("rename", pk), {"name": name + " 2"}).status_code, 200)
            self.assertEqual(self.call(user, "post", self.action("deactivate", pk)).status_code, 200)
            self.assertEqual(self.call(user, "post", self.action("activate", pk)).status_code, 200)

    def test_there_is_no_delete_and_no_put(self):
        for method in ("delete", "put"):
            self.assertEqual(self.call(self.manager, method, self.detail(self.survey.pk), {}).status_code, 405, method)
        self.assertTrue(DocumentType.objects.filter(pk=self.survey.pk).exists())


class CatalogueIsolationTests(CatalogueApiBase):
    def test_another_companys_entries_are_invisible_and_untouchable(self):
        pk = self.b_entry.pk
        before = (DocumentType.objects.filter(company=self.b).count(), DocumentTypeEvent.objects.count())
        self.assertNotIn("Deed", self.names(self.manager))
        self.assertEqual(self.call(self.manager, "get", self.detail(pk)).status_code, 404)
        self.assertEqual(self.call(self.manager, "patch", self.detail(pk), {"description": "hijack"}).status_code, 404)
        for name, payload in (("rename", {"name": "Hijacked"}), ("activate", {}), ("deactivate", {})):
            self.assertEqual(self.call(self.manager, "post", self.action(name, pk), payload).status_code, 404, name)
        self.b_entry.refresh_from_db()
        self.assertEqual((self.b_entry.name, self.b_entry.description, self.b_entry.is_active), ("Deed", "", True))
        self.assertEqual((DocumentType.objects.filter(company=self.b).count(), DocumentTypeEvent.objects.count()), before)

    def test_a_department_from_another_company_cannot_be_used(self):
        response = self.call(self.manager, "post", self.list_url(), {"name": "X", "default_department": self.b_legal.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn("default_department", response.data)
        response = self.call(self.manager, "patch", self.detail(self.survey.pk), {"default_department": self.b_legal.pk})
        self.assertEqual(response.status_code, 400)
        self.assertIn("default_department", response.data)

    def test_the_same_name_may_exist_in_two_companies(self):
        response = self.call(self.manager, "post", self.list_url(), {"name": "Deed"})
        self.assertEqual(response.status_code, 201, response.data)


class CatalogueWriteTests(CatalogueApiBase):
    def test_creating_with_defaults(self):
        response = self.call(self.manager, "post", self.list_url(), {"name": "Title Registration"})
        self.assertEqual(response.status_code, 201, response.data)
        row = response.data
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual((row["sla_days"], row["default_department"], row["drives_legacy_generation"],
                          row["is_active"], row["sort_order"], row["category"]), (90, None, False, True, 0, ""))
        work = ObligationType.objects.get(pk=row["work_type"])
        self.assertEqual((work.company_id, work.auto_generate, work.is_current, work.trigger),
                         (self.a.pk, False, True, "on_sale"))

    def test_bad_input_is_refused_with_the_field_named_and_nothing_is_written(self):
        ObligationType.objects.create(company=self.a, name="Allocation Letter")           # an unlinked settings type
        before = (DocumentType.objects.count(), ObligationType.objects.count(), DocumentTypeEvent.objects.count())
        cases = {
            "name": [{"name": ""}, {"name": "survey PLAN"}, {"name": "allocation letter"}, {"name": "x" * 121}, {}],
            "category": [{"name": "A", "category": "c" * 61}],
            "sla_days": [{"name": "A", "sla_days": 0}, {"name": "A", "sla_days": 3651}, {"name": "A", "sla_days": "x"}],
            "sort_order": [{"name": "A", "sort_order": -1}],
        }
        for field, payloads in cases.items():
            for payload in payloads:
                response = self.call(self.manager, "post", self.list_url(), payload)
                self.assertEqual(response.status_code, 400, payload)
                self.assertIn(field, response.data, payload)
        self.assertEqual((DocumentType.objects.count(), ObligationType.objects.count(),
                          DocumentTypeEvent.objects.count()), before)

    def test_patch_changes_details_and_ignores_the_name(self):
        url = self.detail(self.survey.pk)
        response = self.call(self.manager, "patch", url, {"description": "Surveyed", "name": "Hacked", "sla_days": 45})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["name"], response.data["description"], response.data["sla_days"]),
                         ("Survey Plan", "Surveyed", 45))
        cleared = self.call(self.manager, "patch", url, {"default_department": None})
        self.assertIsNone(cleared.data["default_department"])

    def test_patch_with_nothing_to_change_is_refused(self):
        url = self.detail(self.survey.pk)
        self.assertEqual(self.call(self.manager, "patch", url, {"name": "Hacked"}).status_code, 400)     # only the name
        self.assertEqual(self.call(self.manager, "patch", url, {}).status_code, 400)
        again = self.call(self.manager, "patch", url, {"sla_days": 60})                                   # unchanged
        self.assertEqual(again.status_code, 400)
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.name, "Survey Plan")

    def test_rename(self):
        url = self.action("rename", self.survey.pk)
        ok = self.call(self.manager, "post", url, {"name": "Land Survey"})
        self.assertEqual((ok.status_code, ok.data["name"]), (200, "Land Survey"))
        catalogue.create_document_type(self.a, self.md, name="Other Document")
        for payload in ({"name": ""}, {"name": "other DOCUMENT"}, {"name": "Land Survey"}, {}):      # blank, taken, unchanged
            response = self.call(self.manager, "post", url, payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("name", response.data, payload)

    def test_renaming_a_used_entry_keeps_the_old_name_on_existing_obligations(self):
        obligation = self.used_sale_for(self.survey)
        old_type = obligation.obligation_type
        response = self.call(self.manager, "post", self.action("rename", self.survey.pk), {"name": "Land Survey"})
        self.assertEqual((response.status_code, response.data["name"]), (200, "Land Survey"))
        self.assertNotEqual(response.data["work_type"], old_type.pk)
        obligation.refresh_from_db()
        self.assertEqual((obligation.obligation_type_id, obligation.obligation_type.name), (old_type.pk, "Survey Plan"))

    def test_activate_and_deactivate(self):
        pk = self.survey.pk
        self.assertEqual(self.call(self.manager, "post", self.action("activate", pk)).status_code, 409)     # already active
        off = self.call(self.manager, "post", self.action("deactivate", pk))
        self.assertEqual((off.status_code, off.data["is_active"]), (200, False))
        self.assertEqual(self.call(self.manager, "post", self.action("deactivate", pk)).status_code, 409)
        on = self.call(self.manager, "post", self.action("activate", pk))
        self.assertEqual((on.status_code, on.data["is_active"]), (200, True))

    def test_deactivating_never_changes_the_work_type_settings(self):
        legacy_type = ObligationType.objects.create(company=self.a, name="Legacy", sla_days=90)
        entry = DocumentType.objects.create(company=self.a, name="Legacy")
        ObligationType.objects.filter(pk=legacy_type.pk).update(catalogue_entry=entry, is_current=True)
        self.assertEqual(self.call(self.manager, "post", self.action("deactivate", entry.pk)).status_code, 200)
        legacy_type.refresh_from_db()
        self.assertEqual((legacy_type.is_active, legacy_type.auto_generate), (True, True))

    def test_every_change_is_in_the_history_with_the_actor(self):
        pk = self.survey.pk
        self.call(self.manager, "patch", self.detail(pk), {"description": "x"})
        self.call(self.manager, "post", self.action("rename", pk), {"name": "Land Survey"})
        self.call(self.manager, "post", self.action("deactivate", pk))
        events = list(DocumentTypeEvent.objects.filter(document_type_id=pk).order_by("id"))
        self.assertEqual([e.action for e in events], ["created", "edited", "renamed", "deactivated"])
        self.assertEqual([e.actor_id for e in events[1:]], [self.manager.pk] * 3)
        self.assertEqual(events[2].changes["name"], ["Survey Plan", "Land Survey"])

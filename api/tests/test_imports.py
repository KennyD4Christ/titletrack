from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from importer import parsers
from importer.tests.helpers import D, MAPPING, STATUS_MAPPING, make_company_with_types, make_xlsx
from tracker import services
from tracker.models import ImportBatch, Obligation, Sale, User

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def rows():
    return [
        ["Green Valley", 12, "Ada Obi", 1000, 1000, D(2026, 2, 3), D(2026, 2, 10), "0803 111 2222", "Done", "Pending"],
        ["Green Valley", 13, "Bayo Lawal", 1000, 400, D(2026, 3, 1), None, None, "Pending", None],
        ["Green Valley", 14, "Chidi Eze", "+1+1", 5, D(2026, 3, 1), None, None, None, None],
    ]


class ImportApiBase(APITestCase):
    def setUp(self):
        self.co, self.manager = make_company_with_types()
        self.md = User.objects.create_user("mary", password="x", company=self.co, role="md")
        self.staff = User.objects.create_user("sam", password="x", company=self.co, role="staff")
        self.sales = User.objects.create_user("sola", password="x", company=self.co, role="sales")
        self.viewer = User.objects.create_user("vera", password="x", company=self.co, role="viewer")
        self.other_co, self.other_manager = make_company_with_types("B Homes")
        self.client.force_authenticate(self.manager)

    def upload(self, content=None, name="register.xlsx", **extra):
        content = content if content is not None else make_xlsx(rows())
        return self.client.post(reverse("import-list"), {"file": SimpleUploadedFile(name, content, XLSX), **extra},
                                format="multipart")

    def post(self, name, pk, payload=None):
        return self.client.post(reverse(name, args=[pk]), payload or {}, format="json")

    def to_validated(self):
        pk = self.upload().data["id"]
        self.assertEqual(self.post("import-mapping", pk, {"mapping": MAPPING}).status_code, 200)
        self.assertEqual(self.post("import-status-mapping", pk, {"status_mapping": STATUS_MAPPING}).status_code, 200)
        self.assertEqual(self.post("import-dry-run", pk).status_code, 200)
        return pk


class ImportFlowTests(ImportApiBase):
    def test_full_flow_upload_to_undo(self):
        r = self.upload()
        self.assertEqual(r.status_code, 201, r.data)
        pk = r.data["id"]
        self.assertEqual(r.data["status"], "uploaded")
        self.assertEqual(r.data["headers"][:3], ["Estate", "Plot No", "Buyer Name"])
        self.assertEqual(r.data["row_count"], 3)
        self.assertEqual(r.data["suggested_mapping"]["estate"], "Estate")
        self.assertEqual(r.data["suggested_mapping"]["documents"]["Deed of Assignment"], "Deed Status")
        self.assertIn("Survey Plan", r.data["obligation_types"])
        self.assertEqual(len(r.data["sample_rows"]), 3)
        self.assertTrue(any(f["key"] == "price" and f["required"] for f in r.data["fields"]))
        self.assertNotIn("rows", r.data)                                          # the raw rows are never sent back

        m = self.post("import-mapping", pk, {"mapping": MAPPING})
        self.assertEqual(m.status_code, 200)
        keys = {v["key"]: v for v in m.data["status_values"]}
        self.assertEqual(keys["done"]["suggested"], "completed")

        before = self.post("import-dry-run", pk)
        self.assertEqual(before.data["summary"]["can_commit"], False)             # statuses not mapped yet
        self.assertEqual({u["key"] for u in before.data["unmapped_statuses"]}, {"done", "pending", "(blank)"})

        self.assertEqual(self.post("import-status-mapping", pk, {"status_mapping": STATUS_MAPPING}).status_code, 200)
        dry = self.post("import-dry-run", pk)
        self.assertEqual(dry.data["status"], "validated")
        self.assertEqual(dry.data["summary"]["to_import"], 2)
        self.assertEqual(dry.data["summary"]["errors"], 1)                        # the "TBC" price row
        self.assertTrue(dry.data["summary"]["can_commit"])

        csv = self.client.get(reverse("import-errors-csv", args=[pk]))
        self.assertEqual(csv.status_code, 200)
        self.assertEqual(csv["Content-Type"].split(";")[0], "text/csv")
        body = csv.content.decode()
        self.assertIn("Invalid price", body)
        self.assertIn("'+1+1", body)                                              # formula-like value neutralised in the report

        self.assertEqual(self.post("import-commit", pk, {}).status_code, 400)     # needs explicit confirmation
        done = self.post("import-commit", pk, {"confirm": True})
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(done.data["counts"]["sales_created"], 2)

        listing = self.client.get(reverse("obligation-list"), {"page_size": 200})
        self.assertGreater(listing.data["count"], 0)                              # imported data is visible in the app
        detail = self.client.get(reverse("import-detail", args=[pk]))
        self.assertEqual(detail.data["status"], "committed")
        self.assertEqual(detail.data["summary"]["to_import"], 2)
        self.assertEqual(self.client.get(reverse("import-list")).data["count"], 1)

        self.assertEqual(self.post("import-mapping", pk, {"mapping": MAPPING}).status_code, 400)   # frozen after commit
        undone = self.post("import-undo", pk)
        self.assertEqual(undone.status_code, 200)
        self.assertEqual((Sale.objects.count(), Obligation.objects.count()), (0, 0))
        self.assertEqual(self.client.get(reverse("import-detail", args=[pk])).data["status"], "undone")

    def test_changing_the_mapping_invalidates_the_dry_run(self):
        pk = self.to_validated()
        self.assertEqual(self.client.get(reverse("import-detail", args=[pk])).data["status"], "validated")
        self.post("import-mapping", pk, {"mapping": MAPPING})
        detail = self.client.get(reverse("import-detail", args=[pk])).data
        self.assertEqual(detail["status"], "mapped")
        self.assertIsNone(detail["summary"])
        self.assertEqual(self.post("import-commit", pk, {"confirm": True}).status_code, 400)

    def test_commit_before_dry_run_is_refused(self):
        pk = self.upload().data["id"]
        self.post("import-mapping", pk, {"mapping": MAPPING})
        self.assertEqual(self.post("import-commit", pk, {"confirm": True}).status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

    def test_stale_dry_run_returns_409(self):
        pk = self.to_validated()
        from tracker.models import Buyer, Estate, Plot
        estate = Estate.objects.create(company=self.co, name="Green Valley")
        plot = Plot.objects.create(company=self.co, estate=estate, plot_number="12")
        services.create_sale(self.co, plot, Buyer.objects.create(company=self.co, full_name="Late"), 5, D(2026, 1, 1).date())
        r = self.post("import-commit", pk, {"confirm": True})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Sale.objects.count(), 1)

    def test_undo_blocked_after_edit_returns_409_with_reasons(self):
        pk = self.to_validated()
        self.post("import-commit", pk, {"confirm": True})
        ob = Obligation.objects.filter(sale__import_batch_id=pk).first()
        services.update_obligation(ob, self.manager, note="edited")
        r = self.post("import-undo", pk)
        self.assertEqual(r.status_code, 409)
        self.assertIn("edited after the import", r.data["reasons"][0])
        self.assertEqual(Sale.objects.count(), 2)

    def test_mapping_and_status_mapping_validation(self):
        pk = self.upload().data["id"]
        bad = self.post("import-mapping", pk, {"mapping": {**MAPPING, "price": "Nope"}})
        self.assertEqual(bad.status_code, 400)
        self.assertTrue(bad.data["mapping"])
        self.assertEqual(self.post("import-status-mapping", pk, {"status_mapping": {"done": "completed"}}).status_code, 400)  # map columns first
        self.post("import-mapping", pk, {"mapping": MAPPING})
        self.assertEqual(self.post("import-status-mapping", pk, {"status_mapping": {"done": "finished"}}).status_code, 400)
        self.assertEqual(self.post("import-dry-run", self.upload().data["id"]).status_code, 400)       # no mapping yet


class UploadValidationTests(ImportApiBase):
    def test_bad_files_are_rejected_with_a_clear_message(self):
        for name, content in (("notes.txt", b"hello"), ("data.xlsx", b"not a workbook"), ("empty.csv", b"")):
            r = self.upload(content, name)
            self.assertEqual(r.status_code, 400, name)
            self.assertTrue(r.data.get("detail") or r.data.get("file"), name)     # our message, or DRF's "file is empty"
        self.assertEqual(self.client.post(reverse("import-list"), {}, format="multipart").status_code, 400)
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_row_limit(self):
        with patch.object(parsers, "MAX_ROWS", 2):
            self.assertEqual(self.upload().status_code, 400)

    def test_sheet_selection(self):
        self.assertEqual(self.upload(sheet="Nope").status_code, 400)
        self.assertEqual(self.upload(sheet="Sales").status_code, 201)

    def test_csv_upload(self):
        csv_bytes = b"Estate,Plot No,Buyer Name,Price,Amount Paid\nGV,1,Ada,1000,1000\n"
        r = self.upload(csv_bytes, "register.csv")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["sheets"], ["CSV"])


class ImportPermissionTests(ImportApiBase):
    def test_only_md_and_manager_can_use_imports(self):
        pk = self.upload().data["id"]
        for user, allowed in ((self.manager, True), (self.md, True), (self.staff, False), (self.sales, False), (self.viewer, False)):
            self.client.force_authenticate(user)
            expected_list = 200 if allowed else 403
            self.assertEqual(self.client.get(reverse("import-list")).status_code, expected_list, user.role)
            self.assertEqual(self.client.get(reverse("import-detail", args=[pk])).status_code, expected_list, user.role)
            self.assertEqual(self.upload().status_code, 201 if allowed else 403, user.role)
            self.assertEqual(self.post("import-dry-run", pk).status_code, 400 if allowed else 403, user.role)   # 400: no mapping yet
            self.assertEqual(self.post("import-commit", pk, {"confirm": True}).status_code, 400 if allowed else 403, user.role)

    def test_other_company_cannot_see_or_touch_an_import(self):
        pk = self.upload().data["id"]
        self.client.force_authenticate(self.other_manager)
        self.assertEqual(self.client.get(reverse("import-list")).data["count"], 0)
        self.assertEqual(self.client.get(reverse("import-detail", args=[pk])).status_code, 404)
        for name in ("import-mapping", "import-status-mapping", "import-dry-run", "import-commit", "import-undo"):
            self.assertEqual(self.post(name, pk, {"confirm": True}).status_code, 404, name)
        self.assertEqual(self.client.get(reverse("import-errors-csv", args=[pk])).status_code, 404)

    def test_mapping_cannot_use_another_companys_obligation_types(self):
        pk = self.upload().data["id"]
        self.client.force_authenticate(self.manager)
        r = self.post("import-mapping", pk, {"mapping": {**MAPPING, "documents": {"Made Up Type": "Deed Status"}}})
        self.assertEqual(r.status_code, 400)

    def test_anonymous_and_unsupported_methods(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("import-list")).status_code, 403)
        self.client.force_authenticate(self.manager)
        pk = self.upload().data["id"]
        self.assertEqual(self.client.delete(reverse("import-detail", args=[pk])).status_code, 405)
        self.assertEqual(self.client.patch(reverse("import-detail", args=[pk]), {}, format="json").status_code, 405)

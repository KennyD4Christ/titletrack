"""The inventory write endpoints (/api/inventory/...): MD and Manager register estates and plots, singly or in bulk."""
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from api.tests.inventory_fixtures import InventoryApiBase
from tracker import inventory_services as inv
from tracker import services
from tracker.models import Estate, InventoryEvent, Plot, Sale

A = InventoryEvent.Action


class InventoryApiTestCase(InventoryApiBase):
    def setUp(self):
        super().setUp()
        self.login("md")

    def post(self, name, data, args=None, **extra):
        return self.client.post(reverse(name, args=args), data, format="json", **extra)

    def patch(self, name, data, args):
        return self.client.patch(reverse(name, args=args), data, format="json")

    def events(self, action):
        return InventoryEvent.objects.filter(action=action)

    def labels(self):
        return sorted(Plot.objects.filter(estate=self.estate).values_list("plot_number", flat=True))


class EstateEndpointTests(InventoryApiTestCase):
    def test_create(self):
        response = self.post("inventory-estate-list", {"name": "  Royal   Gardens ", "location": " Ajah "})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data, {"id": response.data["id"], "name": "Royal Gardens", "location": "Ajah"})
        event = self.events(A.ESTATE_CREATED).get(estate_id=response.data["id"])
        self.assertEqual((event.actor, event.company), (self.md, self.a))

    def test_the_location_is_optional(self):
        self.assertEqual(self.post("inventory-estate-list", {"name": "Lakeside"}).data["location"], "")

    def test_a_duplicate_is_409_and_names_the_estate_in_the_way(self):
        response = self.post("inventory-estate-list", {"name": "GREEN   valley"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["existing_estate"], {"id": self.estate.pk, "name": "Green Valley"})
        self.assertIn("already exists", response.data["detail"])
        self.assertEqual(Estate.objects.filter(company=self.a).count(), 1)

    def test_bad_input_is_400(self):
        for body in ({"name": ""}, {"name": "   "}, {"name": "x" * 201}, {"name": "Zero\u200bWidth"}, {}, {"name": None},
                     {"name": "Ok", "location": "y" * 201}):
            response = self.post("inventory-estate-list", body)
            self.assertEqual(response.status_code, 400, body)
        self.assertEqual(Estate.objects.filter(company=self.a).count(), 1)

    def test_another_company_may_use_the_same_name(self):
        self.client.force_authenticate(self.user_b)
        self.assertEqual(self.post("inventory-estate-list", {"name": "green valley"}).status_code, 201)

    def test_edit_changes_only_what_is_sent(self):
        response = self.patch("inventory-estate-detail", {"location": "Ajah"}, [self.estate.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"id": self.estate.pk, "name": "Green Valley", "location": "Ajah"})
        self.assertEqual(self.events(A.ESTATE_EDITED).get().changes, {"location": ["Lekki", "Ajah"]})

    def test_renaming_an_estate_with_sales_needs_a_reason(self):
        self.sell(self.plot("A-1"))
        first = self.patch("inventory-estate-detail", {"name": "Renamed"}, [self.estate.pk])
        self.assertEqual(first.status_code, 400)
        self.assertIn("reason", first.data["detail"])
        second = self.patch("inventory-estate-detail", {"name": "Renamed", "reason": "Rebranded"}, [self.estate.pk])
        self.assertEqual((second.status_code, second.data["name"]), (200, "Renamed"))

    def test_edit_refusals(self):
        other = inv.create_estate(self.a, self.md, name="Royal Gardens")
        self.assertEqual(self.patch("inventory-estate-detail", {}, [self.estate.pk]).status_code, 400)
        self.assertEqual(self.patch("inventory-estate-detail", {"name": "Green Valley"}, [self.estate.pk]).status_code, 400)
        clash = self.patch("inventory-estate-detail", {"name": "green valley"}, [other.pk])
        self.assertEqual((clash.status_code, clash.data["existing_estate"]["id"]), (409, self.estate.pk))

    def test_another_companys_estate_is_404(self):
        self.assertEqual(self.patch("inventory-estate-detail", {"name": "Mine"}, [self.estate_b.pk]).status_code, 404)
        self.assertEqual(Estate.objects.get(pk=self.estate_b.pk).name, "Palm Heights")
        self.assertEqual(self.patch("inventory-estate-detail", {"name": "Mine"}, [999999]).status_code, 404)


class PlotEndpointTests(InventoryApiTestCase):
    def register(self, number, estate=None):
        return self.post("inventory-plot-list", {"estate": (estate or self.estate).pk, "plot_number": number})

    def test_register(self):
        response = self.register("  A  5 ")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data, {"id": response.data["id"], "estate": self.estate.pk,
                                         "estate_name": "Green Valley", "plot_number": "A 5"})
        event = self.events(A.PLOT_REGISTERED).get()
        self.assertEqual((event.actor, event.plot_label), (self.md, "A 5"))

    def test_a_duplicate_is_409_with_the_plot_in_the_way(self):
        first = self.register("A-5")
        response = self.register("a-5")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["existing_plot"], {"id": first.data["id"], "plot_number": "A-5"})
        self.assertEqual(self.labels(), ["A-5"])

    def test_bad_input_is_400_and_another_companys_estate_does_not_exist(self):
        for body in ({"estate": self.estate.pk, "plot_number": ""}, {"estate": self.estate.pk, "plot_number": "A\u200b1"},
                     {"estate": self.estate.pk, "plot_number": "x" * 51}, {"plot_number": "A-1"}, {"estate": self.estate.pk}):
            self.assertEqual(self.post("inventory-plot-list", body).status_code, 400, body)
        foreign = self.register("A-1", estate=self.estate_b)
        self.assertEqual(foreign.status_code, 400)
        self.assertIn("estate", foreign.data)
        self.assertEqual(Plot.objects.count(), 0)

    @override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=1)
    def test_the_cap_is_409_with_the_numbers(self):
        self.register("A-1")
        response = self.register("A-2")
        self.assertEqual(response.status_code, 409)
        self.assertEqual((response.data["limit"], response.data["existing"], response.data["requested"]), (1, 1, 1))

    def test_rename(self):
        plot = self.plot("A-5")
        response = self.patch("inventory-plot-detail", {"plot_number": "A-6"}, [plot.pk])
        self.assertEqual((response.status_code, response.data["plot_number"]), (200, "A-6"))
        self.assertEqual(self.events(A.PLOT_RENAMED).get().changes, {"plot_number": ["A-5", "A-6"]})

    def test_renaming_a_plot_that_ever_had_a_sale_needs_a_reason(self):
        plot = self.plot("A-5")
        sale = self.sell(plot)
        services.cancel_sale(sale, self.md, "buyer withdrew")
        first = self.patch("inventory-plot-detail", {"plot_number": "A-6"}, [plot.pk])
        self.assertEqual(first.status_code, 400)
        second = self.patch("inventory-plot-detail", {"plot_number": "A-6", "reason": "Survey renumbering"}, [plot.pk])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.events(A.PLOT_RENAMED).get().note, "Survey renumbering")

    def test_rename_refusals(self):
        first, second = self.plot("A-5"), self.plot("A-6")
        clash = self.patch("inventory-plot-detail", {"plot_number": " a-5 "}, [second.pk])
        self.assertEqual((clash.status_code, clash.data["existing_plot"]["id"]), (409, first.pk))
        self.assertEqual(self.patch("inventory-plot-detail", {}, [first.pk]).status_code, 400)
        self.assertEqual(self.patch("inventory-plot-detail", {"plot_number": "A-5"}, [first.pk]).status_code, 400)
        self.assertEqual(self.patch("inventory-plot-detail", {"plot_number": "Z-9"}, [999999]).status_code, 404)

    def test_remove(self):
        plot = self.plot("A-5")
        response = self.post("inventory-plot-remove", {"reason": "registered by mistake"}, [plot.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"id": plot.pk, "plot_number": "A-5", "removed": True})
        self.assertFalse(Plot.objects.filter(pk=plot.pk).exists())
        self.assertEqual(self.events(A.PLOT_REMOVED).get().plot_label, "A-5")

    def test_a_plot_with_sales_history_cannot_be_removed(self):
        sold, cancelled = self.plot("A-1"), self.plot("A-2")
        self.sell(sold)
        services.cancel_sale(self.sell(cancelled), self.md, "buyer withdrew")
        for plot in (sold, cancelled):
            response = self.post("inventory-plot-remove", {"reason": "wanted it gone"}, [plot.pk])
            self.assertEqual(response.status_code, 409, plot.plot_number)
            self.assertIn("sales history", response.data["detail"])
            self.assertTrue(Plot.objects.filter(pk=plot.pk).exists())

    def test_remove_refusals(self):
        plot = self.plot("A-5")
        self.assertEqual(self.post("inventory-plot-remove", {"reason": "ab"}, [plot.pk]).status_code, 400)
        self.assertEqual(self.post("inventory-plot-remove", {}, [plot.pk]).status_code, 400)
        self.assertEqual(self.post("inventory-plot-remove", {"reason": "not mine to remove"}, [999999]).status_code, 404)
        self.assertTrue(Plot.objects.filter(pk=plot.pk).exists())

    def test_another_companys_plot_is_404_for_rename_and_remove(self):
        plot_b = self.plot("X-1", estate=self.estate_b, company=self.b, actor=self.user_b)
        self.assertEqual(self.patch("inventory-plot-detail", {"plot_number": "Mine"}, [plot_b.pk]).status_code, 404)
        self.assertEqual(self.post("inventory-plot-remove", {"reason": "not mine"}, [plot_b.pk]).status_code, 404)
        self.assertEqual(Plot.objects.get(pk=plot_b.pk).plot_number, "X-1")

    def test_a_plot_that_vanishes_between_the_lookup_and_the_service_is_404_not_a_server_error(self):
        plot = self.plot("A-5")
        with mock.patch.object(inv, "rename_plot", side_effect=Plot.DoesNotExist):
            self.assertEqual(self.patch("inventory-plot-detail", {"plot_number": "A-6"}, [plot.pk]).status_code, 404)
        with mock.patch.object(inv, "remove_plot", side_effect=Plot.DoesNotExist):
            self.assertEqual(self.post("inventory-plot-remove", {"reason": "registered by mistake"}, [plot.pk]).status_code, 404)
        with mock.patch.object(inv, "preview_bulk", side_effect=Estate.DoesNotExist):
            response = self.post("inventory-plot-bulk-preview", {"estate": self.estate.pk, "entries": [{"labels": ["A1"]}]})
            self.assertEqual(response.status_code, 404)


class BulkEndpointTests(InventoryApiTestCase):
    ENTRIES = [{"prefix": "A", "start": 1, "end": 3}]

    def preview(self, entries=None, estate=None):
        return self.post("inventory-plot-bulk-preview",
                         {"estate": (estate or self.estate).pk, "entries": entries or self.ENTRIES})

    def commit(self, preview, entries=None, estate=None, **override):
        body = {"estate": (estate or self.estate).pk, "entries": entries or self.ENTRIES,
                "input_fingerprint": preview.data["input_fingerprint"],
                "preview_fingerprint": preview.data["preview_fingerprint"], **override}
        return self.post("inventory-plot-bulk-commit", body)

    def test_preview_then_commit(self):
        preview = self.preview()
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["summary"]["new"], 3)
        self.assertTrue(preview.data["can_commit"])
        self.assertEqual([i["outcome"] for i in preview.data["items"]], ["new"] * 3)
        self.assertEqual(Plot.objects.count(), 0)                                    # a preview writes nothing
        committed = self.commit(preview)
        self.assertEqual(committed.status_code, 201, committed.data)
        self.assertEqual(committed.data["created"], 3)
        self.assertEqual(sorted(p["plot_number"] for p in committed.data["plots"]), ["A1", "A2", "A3"])
        self.assertEqual((committed.data["skipped_existing"], committed.data["skipped_repeated"]), ([], []))
        self.assertEqual(self.labels(), ["A1", "A2", "A3"])
        event = self.events(A.PLOTS_BULK_REGISTERED).get()
        self.assertEqual((event.actor, event.changes["count"]), (self.md, 3))

    def test_existing_and_repeated_labels_are_skipped_and_reported(self):
        self.plot("A-1")
        entries = [{"labels": ["A-1", "A-2", "a-2", "A-3"]}]
        committed = self.commit(self.preview(entries), entries=entries)
        self.assertEqual((committed.data["created"], committed.data["skipped_existing"], committed.data["skipped_repeated"]),
                         (2, ["A-1"], ["a-2"]))

    def test_labels_that_are_not_the_previewed_ones_are_400(self):
        preview = self.preview()
        response = self.commit(preview, entries=[{"prefix": "A", "start": 1, "end": 4}])
        self.assertEqual(response.status_code, 400)
        self.assertIn("not the labels that were previewed", response.data["detail"])
        self.assertEqual(Plot.objects.count(), 0)

    def test_a_stale_preview_is_409_and_carries_the_fresh_one(self):
        preview = self.preview()
        self.plot("A2")                                                              # registered after the preview
        response = self.commit(preview)
        self.assertEqual(response.status_code, 409)
        fresh = response.data["preview"]
        self.assertEqual(fresh["summary"]["exists"], 1)
        self.assertNotEqual(fresh["preview_fingerprint"], preview.data["preview_fingerprint"])
        self.assertEqual(self.labels(), ["A2"])
        retry = self.commit(self.preview())                                          # confirming the new preview works
        self.assertEqual((retry.status_code, retry.data["created"]), (201, 2))

    def test_replaying_a_commit_is_409_and_registers_nothing_twice(self):
        preview = self.preview()
        self.assertEqual(self.commit(preview).status_code, 201)
        self.assertEqual(self.commit(preview).status_code, 409)
        self.assertEqual(self.events(A.PLOTS_BULK_REGISTERED).count(), 1)

    def test_invalid_labels_are_reported_by_the_preview_and_refused_by_the_commit(self):
        entries = [{"labels": ["A-1", "bad\u200blabel"]}]
        preview = self.preview(entries)
        self.assertEqual((preview.status_code, preview.data["summary"]["invalid"], preview.data["can_commit"]), (200, 1, False))
        response = self.commit(preview, entries=entries)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Plot.objects.count(), 0)

    @override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=2)
    def test_the_cap_is_409(self):
        preview = self.preview()
        self.assertTrue(preview.data["summary"]["cap_exceeded"])
        self.assertFalse(preview.data["can_commit"])
        response = self.commit(preview)
        self.assertEqual((response.status_code, response.data["limit"], response.data["requested"]), (409, 2, 3))
        self.assertEqual(Plot.objects.count(), 0)

    def test_shapes_and_missing_fields_are_400(self):
        base = {"estate": self.estate.pk}
        for url, body in (
            ("inventory-plot-bulk-preview", {}),
            ("inventory-plot-bulk-preview", base),
            ("inventory-plot-bulk-preview", {**base, "entries": "A1"}),
            ("inventory-plot-bulk-preview", {**base, "entries": ["A1"]}),
            ("inventory-plot-bulk-preview", {**base, "entries": []}),
            ("inventory-plot-bulk-preview", {**base, "entries": [{"start": 5, "end": 1}]}),
            ("inventory-plot-bulk-preview", {**base, "entries": [{"start": 1, "end": 9999}]}),
            ("inventory-plot-bulk-commit", {**base, "entries": self.ENTRIES}),
            ("inventory-plot-bulk-commit", {**base, "entries": self.ENTRIES, "input_fingerprint": "x"}),
            ("inventory-plot-bulk-commit", {**base, "entries": self.ENTRIES, "input_fingerprint": "", "preview_fingerprint": ""}),
        ):
            self.assertEqual(self.post(url, body).status_code, 400, (url, body))
        self.assertEqual(Plot.objects.count(), 0)

    def test_another_companys_estate_does_not_exist_for_bulk(self):
        preview = self.preview()
        for response in (self.preview(estate=self.estate_b), self.commit(preview, estate=self.estate_b)):
            self.assertEqual(response.status_code, 400)
            self.assertIn("estate", response.data)
        self.assertEqual(Plot.objects.count(), 0)

    def test_bulk_registered_plots_work_with_a_sale(self):
        self.commit(self.preview())
        plot = Plot.objects.get(plot_number="A2")
        self.assertEqual(self.sell(plot).plot_id, plot.pk)
        self.assertEqual(Sale.objects.count(), 1)


class ContractTests(InventoryApiTestCase):
    def test_only_post_and_patch_are_routed_and_reading_stays_with_the_reference_endpoints(self):
        plot = self.plot("A-1")
        detail = {"inventory-estate-detail": self.estate.pk, "inventory-plot-detail": plot.pk,
                  "inventory-plot-remove": plot.pk}
        lists = ["inventory-estate-list", "inventory-plot-list", "inventory-plot-bulk-preview", "inventory-plot-bulk-commit"]
        for name in lists:
            for method in ("get", "put", "patch", "delete"):
                self.assertEqual(getattr(self.client, method)(reverse(name), {}, format="json").status_code, 405, (name, method))
        for name, pk in detail.items():
            for method in ("get", "put", "delete"):
                self.assertEqual(getattr(self.client, method)(reverse(name, args=[pk]), {}, format="json").status_code, 405, (name, method))
        self.assertEqual(self.client.post(reverse("inventory-plot-detail", args=[plot.pk]), {}, format="json").status_code, 405)

    def test_the_existing_reference_endpoints_are_still_read_only(self):
        for name in ("estate-list", "plot-list"):
            self.assertEqual(self.client.post(reverse(name), {"name": "x"}, format="json").status_code, 405, name)
        self.assertEqual(self.client.get(reverse("plot-list")).status_code, 200)

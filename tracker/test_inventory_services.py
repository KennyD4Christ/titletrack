"""Estate and plot inventory services (inventory Phase 1, stage 2): tracker/inventory_services.py.

Races are simulated deterministically, never with sleeps: the competing writer is put into the exact gap between
the service's check and its insert (the database then refuses the insert), because that is the case the
service must survive. A real two-connection test for PostgreSQL is at the end.
"""
import threading
from datetime import date
from unittest import mock, skipUnless

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from tracker import inventory_services as inv
from tracker.canonical import canonical_key
from tracker import services
from tracker.models import Buyer, Company, Estate, InventoryEvent, Plot, Sale, User

A = InventoryEvent.Action


def blind_once(manager, matches):
    """Patch manager.filter so the FIRST call whose keyword names are exactly `matches` sees nothing (the other
    writer has not committed yet). Every other call, including later ones, is the real thing."""
    real, state = manager.filter, {"blinded": False}

    def filter_(*args, **kwargs):
        if not state["blinded"] and set(kwargs) == set(matches):
            state["blinded"] = True
            return manager.none()
        return real(*args, **kwargs)

    return mock.patch.object(manager, "filter", side_effect=filter_)


class InventoryBase(TestCase):
    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.b = Company.objects.create(name="B Homes")
        self.mgr = User.objects.create_user("mgr_a", password="x", company=self.a, role="manager")
        self.other_mgr = User.objects.create_user("mgr_b", password="x", company=self.b, role="manager")
        self.estate = inv.create_estate(self.a, self.mgr, name="Green Valley")

    def plot(self, number):
        return inv.register_plot(self.a, self.mgr, estate=self.estate, plot_number=number)

    def sell(self, plot, cancel=False):
        buyer = Buyer.objects.create(company=self.a, full_name="Ada Obi")
        sale = services.create_sale(self.a, plot, buyer, 1000, date(2026, 1, 5))
        if cancel:
            services.cancel_sale(sale, self.mgr, "entered by mistake")
        return sale

    def events(self, action):
        return InventoryEvent.objects.filter(action=action)

    def labels(self, estate=None):
        return sorted(Plot.objects.filter(estate=estate or self.estate).values_list("plot_number", flat=True))

    def bulk(self, entries):
        preview = inv.preview_bulk(self.a, self.mgr, estate=self.estate, entries=entries)
        return preview, dict(input_fingerprint_sent=preview["input_fingerprint"],
                             preview_fingerprint_sent=preview["preview_fingerprint"])

    def commit(self, entries, **fingerprints):
        if not fingerprints:
            _, fingerprints = self.bulk(entries)
        return inv.commit_bulk(self.a, self.mgr, estate=self.estate, entries=entries, **fingerprints)


# ---------- input rules ----------

class InputRuleTests(SimpleTestCase):
    def test_whitespace_is_collapsed_and_case_is_kept(self):
        self.assertEqual(inv.clean_plot_label("  A \t 5\n"), "A 5")
        self.assertEqual(inv.clean_plot_label("a\u00a0 5"), "a 5")
        self.assertEqual(inv.clean_estate_name("  Palm   Grove "), "Palm Grove")

    def test_blank_and_too_long_values_are_refused(self):
        for bad in ("", "   ", None):
            with self.assertRaisesMessage(ValueError, "required"):
                inv.clean_plot_label(bad)
            with self.assertRaisesMessage(ValueError, "required"):
                inv.clean_estate_name(bad)
        with self.assertRaisesMessage(ValueError, "too long"):
            inv.clean_plot_label("x" * 51)
        self.assertEqual(inv.clean_plot_label("x" * 50), "x" * 50)
        with self.assertRaisesMessage(ValueError, "too long"):
            inv.clean_estate_name("x" * 201)

    def test_invisible_and_control_characters_are_refused_with_their_code_points(self):
        cases = {"A\u200b5": "U+200B", "A\u202e5": "U+202E", "A\u00ad5": "U+00AD", "A\x075": "U+0007",
                 "\ufeffA5": "U+FEFF", "A\u200d5": "U+200D"}
        for text, code in cases.items():
            with self.assertRaises(ValueError) as caught:
                inv.clean_plot_label(text)
            self.assertIn(code, str(caught.exception), text)
            with self.assertRaises(ValueError):
                inv.clean_estate_name(text)

    def test_ordinary_punctuation_digits_and_accents_are_accepted_unchanged(self):
        for text in ("A-5", "12/B", "Plot #7", "A.5", "Ìbàdàn 3", "05", "Block-C, 12"):
            self.assertEqual(inv.clean_plot_label(text), text)

    def test_location_is_optional_but_still_checked(self):
        self.assertEqual(inv.clean_location(None), "")
        self.assertEqual(inv.clean_location("  Lekki   Phase 1 "), "Lekki Phase 1")
        with self.assertRaises(ValueError):
            inv.clean_location("x" * 201)
        with self.assertRaises(ValueError):
            inv.clean_location("Lekki\u200b")

    def test_a_reason_needs_three_characters(self):
        self.assertEqual(inv._reason("  typo  "), "typo")
        for bad in ("", "  ", "ab", None):
            with self.assertRaises(ValueError):
                inv._reason(bad)


# ---------- the audit table ----------

class InventoryEventTests(InventoryBase):
    def test_history_cannot_be_changed_or_deleted(self):
        event = self.events(A.ESTATE_CREATED).get()
        event.note = "changed"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()

    def test_history_survives_the_removal_of_its_plot_and_estate_with_names_kept(self):
        plot = self.plot("A-5")
        inv.remove_plot(plot, self.mgr, reason="registered twice by mistake")
        registered = self.events(A.PLOT_REGISTERED).get()
        self.assertIsNone(registered.plot)
        self.assertEqual(registered.plot_label, "A-5")
        self.estate.delete()                              # an import undo (or an operator) removes the estate
        created = self.events(A.ESTATE_CREATED).get()
        self.assertIsNone(created.estate)
        self.assertEqual(created.estate_name, "Green Valley")
        self.assertEqual(InventoryEvent.objects.count(), 3)

    def test_the_company_admin_view_is_read_only(self):
        ma = admin.site._registry[InventoryEvent]
        request = mock.Mock(user=User(is_superuser=True))
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_change_permission(request))
        self.assertFalse(ma.has_delete_permission(request))


# ---------- estates ----------

class EstateTests(InventoryBase):
    def test_create_records_the_estate_and_its_event(self):
        estate = inv.create_estate(self.a, self.mgr, name="  Royal   Gardens ", location=" Lekki ")
        self.assertEqual((estate.name, estate.location, estate.name_key), ("Royal Gardens", "Lekki", "royal gardens"))
        event = self.events(A.ESTATE_CREATED).get(estate=estate)
        self.assertEqual((event.actor, event.company, event.estate_name), (self.mgr, self.a, "Royal Gardens"))
        self.assertEqual(event.changes["name"], [None, "Royal Gardens"])

    def test_a_duplicate_under_the_canonical_key_is_refused_with_the_existing_estate(self):
        for variant in ("green valley", "GREEN   VALLEY", " Green Valley "):
            with self.assertRaises(inv.DuplicateEstate) as caught:
                inv.create_estate(self.a, self.mgr, name=variant)
            self.assertEqual(caught.exception.estate, self.estate)
        self.assertEqual(Estate.objects.filter(company=self.a).count(), 1)

    def test_the_same_name_is_fine_in_another_company(self):
        other = inv.create_estate(self.b, self.other_mgr, name="green valley")
        self.assertEqual(other.company, self.b)

    def test_another_companys_user_is_refused(self):
        with self.assertRaises(PermissionDenied):
            inv.create_estate(self.a, self.other_mgr, name="Sneaky")

    def test_bad_input_is_refused_before_anything_is_written(self):
        before = (Estate.objects.count(), InventoryEvent.objects.count())
        for name, location in (("", ""), ("x" * 201, ""), ("Fine", "x" * 201), ("Zero\u200bWidth", "")):
            with self.assertRaises(ValueError):
                inv.create_estate(self.a, self.mgr, name=name, location=location)
        self.assertEqual((Estate.objects.count(), InventoryEvent.objects.count()), before)

    def test_a_writer_that_gets_in_between_the_check_and_the_insert_gives_a_duplicate_not_an_error(self):
        with blind_once(Estate.objects, {"company", "name_key"}):
            with self.assertRaises(inv.DuplicateEstate) as caught:
                inv.create_estate(self.a, self.mgr, name="green  valley")
        self.assertEqual(caught.exception.estate, self.estate)
        self.assertEqual(Estate.objects.filter(company=self.a).count(), 1)

    def test_edit_changes_only_what_is_passed_and_records_before_and_after(self):
        edited = inv.edit_estate(self.estate, self.mgr, location="Ajah")
        self.assertEqual((edited.name, edited.location), ("Green Valley", "Ajah"))
        event = self.events(A.ESTATE_EDITED).get()
        self.assertEqual(event.changes, {"location": ["", "Ajah"]})

    def test_a_rename_without_sales_needs_no_reason_and_updates_the_key(self):
        edited = inv.edit_estate(self.estate, self.mgr, name="Green Valley Phase 2")
        self.assertEqual(Estate.objects.get(pk=edited.pk).name_key, "green valley phase 2")
        self.assertEqual(self.events(A.ESTATE_EDITED).get().estate_name, "Green Valley Phase 2")

    def test_a_rename_of_an_estate_with_sales_history_needs_a_reason(self):
        self.sell(self.plot("A-1"), cancel=True)                      # even a cancelled sale counts
        with self.assertRaisesMessage(ValueError, "reason"):
            inv.edit_estate(self.estate, self.mgr, name="Renamed")
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name, "Green Valley")
        inv.edit_estate(self.estate, self.mgr, name="Renamed", reason="Rebranded by the developer")
        self.assertEqual(self.events(A.ESTATE_EDITED).get().note, "Rebranded by the developer")

    def test_editing_only_the_location_of_an_estate_with_sales_needs_no_reason(self):
        self.sell(self.plot("A-1"))
        inv.edit_estate(self.estate, self.mgr, location="Epe")

    def test_a_case_only_rename_is_allowed(self):
        inv.edit_estate(self.estate, self.mgr, name="GREEN VALLEY")
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name, "GREEN VALLEY")

    def test_renaming_into_another_estates_name_is_refused(self):
        other = inv.create_estate(self.a, self.mgr, name="Royal Gardens")
        with self.assertRaises(inv.DuplicateEstate) as caught:
            inv.edit_estate(other, self.mgr, name="green   valley")
        self.assertEqual(caught.exception.estate, self.estate)
        self.assertEqual(Estate.objects.get(pk=other.pk).name, "Royal Gardens")

    def test_a_rename_that_loses_a_race_gives_a_duplicate_not_an_error(self):
        other = inv.create_estate(self.a, self.mgr, name="Royal Gardens")
        with blind_once(Estate.objects, {"company_id", "name_key"}):
            with self.assertRaises(inv.DuplicateEstate):
                inv.edit_estate(other, self.mgr, name="Green  Valley")

    def test_nothing_to_change_and_wrong_company(self):
        with self.assertRaisesMessage(ValueError, "Nothing to change"):
            inv.edit_estate(self.estate, self.mgr, name="  Green   Valley ", location="")
        with self.assertRaises(PermissionDenied):
            inv.edit_estate(self.estate, self.other_mgr, name="Mine now")
        self.assertEqual(Estate.objects.get(pk=self.estate.pk).name, "Green Valley")


# ---------- single plots ----------

class PlotRegistrationTests(InventoryBase):
    def test_register_creates_an_unsold_plot_and_its_event(self):
        plot = self.plot("  A  -  5 ")
        self.assertEqual((plot.plot_number, plot.plot_key, plot.company, plot.estate), ("A - 5", "a - 5", self.a, self.estate))
        self.assertFalse(Sale.objects.filter(plot=plot).exists())
        event = self.events(A.PLOT_REGISTERED).get()
        self.assertEqual((event.plot, event.estate, event.actor, event.plot_label), (plot, self.estate, self.mgr, "A - 5"))

    def test_a_duplicate_under_the_canonical_key_is_refused_with_the_existing_plot(self):
        first = self.plot("A-5")
        for variant in ("a-5", " A-5 ", "A-5\t"):
            with self.assertRaises(inv.DuplicatePlot) as caught:
                self.plot(variant)
            self.assertEqual(caught.exception.plot, first)
        self.assertEqual(self.labels(), ["A-5"])

    def test_numbers_that_merely_look_alike_are_different_plots(self):
        for number in ("5", "05", "A-5", "A5"):
            self.plot(number)
        self.assertEqual(len(self.labels()), 4)

    def test_the_same_number_is_fine_in_another_estate(self):
        second = inv.create_estate(self.a, self.mgr, name="Royal Gardens")
        self.plot("A-5")
        inv.register_plot(self.a, self.mgr, estate=second, plot_number="a-5")

    def test_another_companys_estate_or_user_is_refused_and_nothing_is_written(self):
        with self.assertRaises(PermissionDenied):
            inv.register_plot(self.a, self.other_mgr, estate=self.estate, plot_number="A-1")
        with self.assertRaises(PermissionDenied):
            inv.register_plot(self.b, self.other_mgr, estate=self.estate, plot_number="A-1")
        self.assertEqual(Plot.objects.count(), 0)

    def test_bad_labels_are_refused(self):
        for bad in ("", "  ", "x" * 51, "A\u200b1"):
            with self.assertRaises(ValueError):
                self.plot(bad)
        self.assertEqual(Plot.objects.count(), 0)

    def test_the_cap_applies_to_registration(self):
        with override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=2):
            self.plot("A-1")
            self.plot("A-2")
            with self.assertRaises(inv.PlotCapExceeded) as caught:
                self.plot("A-3")
        self.assertEqual((caught.exception.limit, caught.exception.existing), (2, 2))
        self.assertEqual(len(self.labels()), 2)

    def test_the_cap_does_not_stop_sales_that_create_plots(self):
        with override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=1):
            self.plot("A-1")
            services.add_sale(self.a, self.mgr, plot_number="A-2", price="1000", sale_date=date.today(),
                              estate=self.estate, buyer_name="Ada Obi")
        self.assertEqual(len(self.labels()), 2)

    def test_the_default_cap_is_3000(self):
        self.assertEqual(inv.max_plots_per_estate(), 3000)

    def test_a_writer_that_gets_in_between_the_check_and_the_insert_gives_a_duplicate_not_an_error(self):
        winner = self.plot("A-5")
        with blind_once(Plot.objects, {"estate", "plot_key"}):
            with self.assertRaises(inv.DuplicatePlot) as caught:
                self.plot("a-5")
        self.assertEqual(caught.exception.plot, winner)
        self.assertEqual(self.labels(), ["A-5"])

    def test_a_registered_plot_is_reused_by_add_sale_whatever_its_case(self):
        plot = self.plot("A-5")
        sale = services.add_sale(self.a, self.mgr, plot_number="a-5", price="1000", sale_date=date.today(),
                                 estate=self.estate, buyer_name="Ada Obi")
        self.assertEqual(sale.plot, plot)
        self.assertEqual(self.labels(), ["A-5"])

    def test_a_plot_created_by_a_sale_blocks_registering_a_variant(self):
        services.add_sale(self.a, self.mgr, plot_number="B-7", price="1000", sale_date=date.today(),
                          estate=self.estate, buyer_name="Ada Obi")
        with self.assertRaises(inv.DuplicatePlot):
            self.plot("b-7")


# ---------- rename and removal ----------

class RenameAndRemoveTests(InventoryBase):
    def test_rename_records_before_and_after_and_updates_the_key(self):
        plot = inv.rename_plot(self.plot("A-5"), self.mgr, plot_number="A-6", reason="typo")
        self.assertEqual(Plot.objects.get(pk=plot.pk).plot_key, "a-6")
        event = self.events(A.PLOT_RENAMED).get()
        self.assertEqual((event.changes, event.note), ({"plot_number": ["A-5", "A-6"]}, "typo"))

    def test_a_rename_without_sales_needs_no_reason(self):
        inv.rename_plot(self.plot("A-5"), self.mgr, plot_number="A-6")

    def test_a_rename_of_a_plot_that_ever_had_a_sale_needs_a_reason_and_the_sale_follows(self):
        for cancel in (False, True):
            plot = self.plot("S-1" if not cancel else "S-2")
            sale = self.sell(plot, cancel=cancel)
            with self.assertRaisesMessage(ValueError, "reason"):
                inv.rename_plot(plot, self.mgr, plot_number=plot.plot_number + "X")
            renamed = inv.rename_plot(plot, self.mgr, plot_number=plot.plot_number + "X", reason="Survey renumbering")
            self.assertEqual(Sale.objects.get(pk=sale.pk).plot, renamed)

    def test_a_case_only_rename_is_allowed_and_a_clash_is_refused(self):
        plot = self.plot("a-5")
        inv.rename_plot(plot, self.mgr, plot_number="A-5")
        other = self.plot("B-1")
        with self.assertRaises(inv.DuplicatePlot) as caught:
            inv.rename_plot(other, self.mgr, plot_number="  a-5 ")
        self.assertEqual(caught.exception.plot.pk, plot.pk)
        self.assertEqual(Plot.objects.get(pk=other.pk).plot_number, "B-1")

    def test_a_rename_that_loses_a_race_gives_a_duplicate_not_an_error(self):
        self.plot("A-5")
        other = self.plot("B-1")
        with blind_once(Plot.objects, {"estate_id", "plot_key"}):
            with self.assertRaises(inv.DuplicatePlot):
                inv.rename_plot(other, self.mgr, plot_number="a-5")

    def test_rename_nothing_to_change_bad_label_and_wrong_company(self):
        plot = self.plot("A-5")
        with self.assertRaisesMessage(ValueError, "Nothing to change"):
            inv.rename_plot(plot, self.mgr, plot_number=" A-5 ")
        with self.assertRaises(ValueError):
            inv.rename_plot(plot, self.mgr, plot_number="A\u200b5")
        with self.assertRaises(PermissionDenied):
            inv.rename_plot(plot, self.other_mgr, plot_number="Z-9")

    def test_an_unsold_plot_can_be_removed_and_the_history_keeps_its_number(self):
        plot = self.plot("A-5")
        inv.remove_plot(plot, self.mgr, reason="registered by mistake")
        self.assertFalse(Plot.objects.filter(pk=plot.pk).exists())
        event = self.events(A.PLOT_REMOVED).get()
        self.assertEqual((event.plot_label, event.note, event.estate), ("A-5", "registered by mistake", self.estate))
        self.plot("A-5")                                              # the number is free again

    def test_a_plot_that_ever_had_a_sale_cannot_be_removed_even_if_the_sale_was_cancelled(self):
        for cancel in (False, True):
            plot = self.plot("S-1" if not cancel else "S-2")
            self.sell(plot, cancel=cancel)
            with self.assertRaises(inv.RemovalBlocked):
                inv.remove_plot(plot, self.mgr, reason="wanted it gone")
            self.assertTrue(Plot.objects.filter(pk=plot.pk).exists())
        self.assertFalse(self.events(A.PLOT_REMOVED).exists())

    def test_removal_needs_a_reason_and_the_right_company(self):
        plot = self.plot("A-5")
        with self.assertRaisesMessage(ValueError, "reason"):
            inv.remove_plot(plot, self.mgr, reason="  ")
        with self.assertRaises(PermissionDenied):
            inv.remove_plot(plot, self.other_mgr, reason="not yours")
        self.assertTrue(Plot.objects.filter(pk=plot.pk).exists())


# ---------- bulk: expanding the request ----------

class ExpandEntriesTests(SimpleTestCase):
    def test_a_list_of_labels_and_text_with_one_label_per_line(self):
        self.assertEqual(inv.expand_entries([{"labels": ["A1", " b  2 ", "", "  "]}]), ["A1", "b 2"])
        self.assertEqual(inv.expand_entries([{"labels": "A1\n\n  A2  \nA3"}]), ["A1", "A2", "A3"])

    def test_ranges_with_prefix_suffix_and_padding(self):
        self.assertEqual(inv.expand_entries([{"prefix": "A-", "start": 1, "end": 3}]), ["A-1", "A-2", "A-3"])
        self.assertEqual(inv.expand_entries([{"prefix": "P", "start": 8, "end": 10, "pad": 3, "suffix": "x"}]),
                         ["P008x", "P009x", "P010x"])
        self.assertEqual(inv.expand_entries([{"start": "5", "end": "6"}]), ["5", "6"])

    def test_several_entries_cover_estates_numbered_in_blocks(self):
        out = inv.expand_entries([{"prefix": "A", "start": 1, "end": 2}, {"prefix": "B", "start": 1, "end": 2},
                                  {"labels": ["Gate House"]}])
        self.assertEqual(out, ["A1", "A2", "B1", "B2", "Gate House"])

    def test_equivalent_requests_expand_to_the_same_labels(self):
        self.assertEqual(inv.expand_entries([{"prefix": "A", "start": 1, "end": 3}]),
                         inv.expand_entries([{"labels": ["A1", "A2", "A3"]}]))

    def test_limits_and_bad_shapes_are_refused(self):
        bad = [
            None, [], "A1", [{}], [{"labels": 5}], [{"start": 1}], [{"start": 5, "end": 1}], [{"start": -1, "end": 3}],
            [{"start": "x", "end": 3}], [{"start": True, "end": 3}], [{"start": 1, "end": 2, "pad": 11}],
            [{"start": 1, "end": 2, "prefix": "p" * 51}], [{"start": 1, "end": inv.BULK_MAX_LABELS + 1}],
            [{"labels": ["a"] * 300}, {"labels": ["b"] * 201}], [{"labels": ["  "]}], ["A1"],
        ]
        for entries in bad:
            with self.assertRaises(ValueError, msg=repr(entries)):
                inv.expand_entries(entries)

    def test_exactly_the_maximum_is_allowed_and_a_huge_range_is_refused_before_it_is_expanded(self):
        self.assertEqual(len(inv.expand_entries([{"start": 1, "end": inv.BULK_MAX_LABELS}])), inv.BULK_MAX_LABELS)
        with mock.patch.object(inv, "collapse", side_effect=AssertionError("expanded")):
            with self.assertRaises(ValueError):
                inv.expand_entries([{"start": 1, "end": 10 ** 12}])


# ---------- bulk: preview ----------

class BulkPreviewTests(InventoryBase):
    def test_every_outcome(self):
        self.plot("A-1")
        preview, _ = self.bulk([{"labels": ["A-1", "a-1", "A-2", " a-2", "B 3", "x" * 51, "Z\u200b9"]}])
        outcomes = [item["outcome"] for item in preview["items"]]
        self.assertEqual(outcomes, ["exists", "duplicate_in_request", "new", "duplicate_in_request", "new",
                                    "invalid", "invalid"])
        self.assertEqual(preview["summary"]["new"], 2)
        self.assertEqual((preview["summary"]["exists"], preview["summary"]["invalid"]), (1, 2))
        self.assertIsNotNone(preview["items"][0]["existing_plot"])
        self.assertIn("item 3", preview["items"][3]["message"])
        self.assertFalse(preview["can_commit"])                      # invalid labels block it

    def test_a_clean_preview_can_be_committed(self):
        preview, _ = self.bulk([{"prefix": "A", "start": 1, "end": 5}])
        self.assertTrue(preview["can_commit"])
        self.assertEqual(preview["summary"], {
            "total": 5, "new": 5, "exists": 0, "duplicate_in_request": 0, "invalid": 0, "warnings": 0,
            "existing_plots": 0, "limit": 3000, "cap_exceeded": False})

    def test_a_preview_writes_nothing_and_takes_no_event(self):
        before = (Plot.objects.count(), InventoryEvent.objects.count())
        self.bulk([{"prefix": "A", "start": 1, "end": 20}])
        self.assertEqual((Plot.objects.count(), InventoryEvent.objects.count()), before)

    def test_leading_zero_lookalikes_warn_but_do_not_block(self):
        self.plot("5")
        preview, _ = self.bulk([{"labels": ["05", "A-7", "A-07", "100", "1000"]}])
        by_label = {item["label"]: item for item in preview["items"]}
        self.assertEqual(by_label["05"]["outcome"], "new")
        self.assertIn("existing plot '5'", by_label["05"]["warnings"][0])
        self.assertIn("'A-7' in this request", by_label["A-07"]["warnings"][0])
        self.assertEqual(by_label["100"]["warnings"] + by_label["1000"]["warnings"], [])
        self.assertTrue(preview["can_commit"])
        self.assertEqual(preview["summary"]["warnings"], 2)

    def test_the_cap_is_reported(self):
        with override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=3):
            self.plot("A-1")
            preview, _ = self.bulk([{"prefix": "B", "start": 1, "end": 3}])
        self.assertTrue(preview["summary"]["cap_exceeded"])
        self.assertFalse(preview["can_commit"])

    def test_fingerprints_depend_on_what_matters_and_nothing_else(self):
        def fp(entries):
            p = inv.preview_bulk(self.a, self.mgr, estate=self.estate, entries=entries)
            return p["input_fingerprint"], p["preview_fingerprint"]

        by_range = fp([{"prefix": "A", "start": 1, "end": 3}])
        self.assertEqual(by_range, fp([{"labels": ["A1", "A2", "A3"]}]))             # same labels, same input
        self.assertEqual(by_range, fp([{"labels": ["A1", " A2 ", "A3"]}]))           # spacing is collapsed first
        self.assertEqual(len(by_range[0]), 64)
        for changed in ([{"labels": ["A1", "A2", "A4"]}], [{"labels": ["A1", "A3", "A2"]}],
                        [{"labels": ["A1", "a2", "A3"]}], [{"labels": ["A1", "A2"]}]):
            self.assertNotEqual(by_range[0], fp(changed)[0], changed)                # edited, reordered, recased, removed

    def test_the_input_fingerprint_is_tied_to_the_company_and_estate(self):
        other = inv.create_estate(self.a, self.mgr, name="Royal Gardens")
        one = inv.input_fingerprint(self.a.pk, self.estate.pk, ["A1"])
        self.assertNotEqual(one, inv.input_fingerprint(self.a.pk, other.pk, ["A1"]))
        self.assertNotEqual(one, inv.input_fingerprint(self.b.pk, self.estate.pk, ["A1"]))

    def test_another_company_cannot_preview_this_estate(self):
        with self.assertRaises(PermissionDenied):
            inv.preview_bulk(self.b, self.other_mgr, estate=self.estate, entries=[{"labels": ["A1"]}])
        with self.assertRaises(PermissionDenied):
            inv.preview_bulk(self.a, self.other_mgr, estate=self.estate, entries=[{"labels": ["A1"]}])


# ---------- bulk: commit ----------

class BulkCommitTests(InventoryBase):
    def test_commit_registers_the_new_labels_skips_the_rest_and_writes_one_event(self):
        self.plot("A-1")
        entries = [{"labels": ["A-1", "A-2", "a-2", "A-3"]}]
        result = self.commit(entries)
        self.assertEqual(self.labels(), ["A-1", "A-2", "A-3"])
        self.assertEqual((len(result.created), result.skipped_existing, result.skipped_repeated),
                         (2, ["A-1"], ["a-2"]))
        event = self.events(A.PLOTS_BULK_REGISTERED).get()
        self.assertEqual((event.estate, event.actor, event.changes["count"]), (self.estate, self.mgr, 2))
        self.assertEqual(event.changes["labels"], ["A-2", "A-3"])
        self.assertEqual(len(event.changes["input_fingerprint"]), 64)
        self.assertFalse(self.events(A.PLOT_REGISTERED).filter(plot_label="A-2").exists())   # one event, not one per plot

    def test_commit_never_changes_an_existing_plot(self):
        existing = self.plot("A-1")
        self.sell(existing)
        self.commit([{"labels": ["a-1", "A-2"]}])
        self.assertEqual(Plot.objects.get(pk=existing.pk).plot_number, "A-1")
        self.assertEqual(Sale.objects.count(), 1)

    def test_a_range_commit_with_padding(self):
        self.commit([{"prefix": "P", "start": 1, "end": 120, "pad": 3}])
        self.assertEqual(Plot.objects.filter(estate=self.estate).count(), 120)
        self.assertTrue(Plot.objects.filter(estate=self.estate, plot_number="P001").exists())

    def test_the_new_plots_are_inserted_in_key_order(self):
        seen = []
        real = inv._insert_plots
        with mock.patch.object(inv, "_insert_plots", side_effect=lambda plots: (seen.extend(
                canonical_key(p.plot_number) for p in plots), real(plots))[1]):
            self.commit([{"labels": ["c-3", "A-1", "b-2", "A-0"]}])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen, ["a-0", "a-1", "b-2", "c-3"])

    def test_the_commit_refuses_labels_that_are_not_the_previewed_input(self):
        _, sent = self.bulk([{"prefix": "A", "start": 1, "end": 3}])
        for tampered in ([{"prefix": "A", "start": 1, "end": 4}], [{"labels": ["A1", "A2", "A9"]}],
                         [{"labels": ["A3", "A2", "A1"]}]):
            with self.assertRaisesMessage(ValueError, "not the labels that were previewed"):
                self.commit(tampered, **sent)
        self.assertEqual(Plot.objects.count(), 0)

    def test_a_preview_for_another_estate_cannot_be_replayed_against_this_one(self):
        other = inv.create_estate(self.a, self.mgr, name="Royal Gardens")
        entries = [{"labels": ["A1"]}]
        preview = inv.preview_bulk(self.a, self.mgr, estate=other, entries=entries)
        with self.assertRaises(ValueError):
            inv.commit_bulk(self.a, self.mgr, estate=self.estate, entries=entries,
                            input_fingerprint_sent=preview["input_fingerprint"],
                            preview_fingerprint_sent=preview["preview_fingerprint"])

    def test_the_commit_needs_both_fingerprints(self):
        preview, sent = self.bulk([{"labels": ["A1"]}])
        for missing in ("input_fingerprint_sent", "preview_fingerprint_sent"):
            with self.assertRaisesMessage(ValueError, "Preview the labels first"):
                self.commit([{"labels": ["A1"]}], **{**sent, missing: ""})
        with self.assertRaises(ValueError):
            self.commit([{"labels": ["A1"]}], input_fingerprint_sent="0" * 64, preview_fingerprint_sent="0" * 64)

    def test_a_label_that_became_existing_since_the_preview_is_a_stale_preview(self):
        entries = [{"labels": ["A-1", "A-2", "A-3"]}]
        _, sent = self.bulk(entries)
        self.plot("A-2")                                  # someone registered one of them after the preview
        with self.assertRaises(inv.StalePreview) as caught:
            self.commit(entries, **sent)
        fresh = caught.exception.preview
        self.assertEqual(fresh["summary"]["exists"], 1)
        self.assertNotEqual(fresh["preview_fingerprint"], sent["preview_fingerprint_sent"])
        self.assertEqual(self.labels(), ["A-2"])          # nothing else was registered
        self.assertFalse(self.events(A.PLOTS_BULK_REGISTERED).exists())

    def test_the_swap_case_where_the_totals_do_not_change_is_still_caught(self):
        """The case a bare `expected_new` count misses: one label stops being new and another starts, so the
        number of new plots is the same. The fingerprint of the outcomes is not."""
        existing = self.plot("A-2")
        entries = [{"labels": ["A-1", "A-2"]}]
        preview, sent = self.bulk(entries)
        self.assertEqual((preview["summary"]["new"], preview["summary"]["exists"]), (1, 1))
        inv.remove_plot(existing, self.mgr, reason="registered by mistake")          # A-2 is new again ...
        self.plot("A-1")                                                              # ... and A-1 now exists
        fresh = inv.preview_bulk(self.a, self.mgr, estate=self.estate, entries=entries)
        self.assertEqual((fresh["summary"]["new"], fresh["summary"]["exists"]), (1, 1))   # same totals as before
        with self.assertRaises(inv.StalePreview):
            self.commit(entries, **sent)
        self.assertEqual(self.labels(), ["A-1"])

    def test_replaying_a_commit_registers_nothing_twice(self):
        entries = [{"labels": ["A-1", "A-2"]}]
        _, sent = self.bulk(entries)
        self.commit(entries, **sent)
        with self.assertRaises(inv.StalePreview):
            self.commit(entries, **sent)
        self.assertEqual(self.labels(), ["A-1", "A-2"])
        self.assertEqual(self.events(A.PLOTS_BULK_REGISTERED).count(), 1)

    def test_a_removal_after_the_preview_is_also_a_stale_preview(self):
        existing = self.plot("A-1")
        entries = [{"labels": ["A-1", "A-2"]}]
        _, sent = self.bulk(entries)
        inv.remove_plot(existing, self.mgr, reason="registered by mistake")
        with self.assertRaises(inv.StalePreview):
            self.commit(entries, **sent)

    def test_a_rename_after_the_preview_is_a_stale_preview(self):
        existing = self.plot("A-1")
        entries = [{"labels": ["A-1", "A-2"]}]
        _, sent = self.bulk(entries)
        inv.rename_plot(existing, self.mgr, plot_number="A-9")
        with self.assertRaises(inv.StalePreview):
            self.commit(entries, **sent)

    def test_invalid_labels_block_the_commit_and_nothing_is_written(self):
        entries = [{"labels": ["A-1", "bad\u200blabel"]}]
        with self.assertRaisesMessage(ValueError, "invalid"):
            self.commit(entries)
        self.assertEqual(Plot.objects.count(), 0)

    def test_a_request_with_nothing_new_is_refused(self):
        self.plot("A-1")
        with self.assertRaisesMessage(ValueError, "Nothing to register"):
            self.commit([{"labels": ["a-1", "A-1"]}])

    def test_the_cap_is_rechecked_inside_the_commit(self):
        entries = [{"prefix": "B", "start": 1, "end": 3}]
        with override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=5):
            preview, sent = self.bulk(entries)
            self.assertTrue(preview["can_commit"])
            for number in ("A-1", "A-2", "A-3"):
                self.plot(number)                         # the estate fills up after the preview ...
            fresh = inv.preview_bulk(self.a, self.mgr, estate=self.estate, entries=entries)
            self.assertTrue(fresh["summary"]["cap_exceeded"])
            self.assertEqual(fresh["preview_fingerprint"], sent["preview_fingerprint_sent"])   # ... outcomes unchanged
            with self.assertRaises(inv.PlotCapExceeded) as caught:
                self.commit(entries, **sent)
        self.assertEqual((caught.exception.limit, caught.exception.existing, caught.exception.requested), (5, 3, 3))
        self.assertEqual(len(self.labels()), 3)

    def test_exactly_filling_the_cap_is_allowed(self):
        with override_settings(INVENTORY_MAX_PLOTS_PER_ESTATE=4):
            self.plot("A-1")
            self.commit([{"prefix": "B", "start": 1, "end": 3}])
        self.assertEqual(len(self.labels()), 4)

    def test_a_writer_that_takes_no_lock_and_gets_into_the_gap_rolls_everything_back(self):
        """A sale or an import creates one of the plots after the final check and before our insert. The database
        refuses the whole multi-row insert; no plot and no event is left behind."""
        entries = [{"labels": ["A-1", "A-2", "A-3"]}]
        _, sent = self.bulk(entries)
        stale = inv.classify_labels(self.estate, inv.expand_entries(entries))      # what the commit's check will see
        Plot.objects.create(company=self.a, estate=self.estate, plot_number="a-2")        # the competing writer
        with mock.patch.object(inv, "classify_labels", return_value=stale):
            with self.assertRaises(inv.RegistrationRace):
                self.commit(entries, **sent)
        self.assertEqual(self.labels(), ["a-2"])                                    # A-1 and A-3 were not kept
        self.assertFalse(self.events(A.PLOTS_BULK_REGISTERED).exists())

    def test_another_company_cannot_commit_into_this_estate(self):
        entries = [{"labels": ["A1"]}]
        _, sent = self.bulk(entries)
        with self.assertRaises(PermissionDenied):
            inv.commit_bulk(self.b, self.other_mgr, estate=self.estate, entries=entries, **sent)
        with self.assertRaises(PermissionDenied):
            inv.commit_bulk(self.a, self.other_mgr, estate=self.estate, entries=entries, **sent)
        self.assertEqual(Plot.objects.count(), 0)

    def test_bulk_registered_plots_are_ordinary_plots_that_a_sale_can_use(self):
        self.commit([{"prefix": "A", "start": 1, "end": 3}])
        sale = services.add_sale(self.a, self.mgr, plot_number="a2", price="1000", sale_date=date.today(),
                                 estate=self.estate, buyer_name="Ada Obi")
        self.assertEqual(sale.plot.plot_number, "A2")
        self.assertEqual(Plot.objects.filter(estate=self.estate).count(), 3)


# ---------- two real connections (PostgreSQL) ----------

@skipUnless(connection.vendor == "postgresql", "needs a database with row-level concurrency")
class RealConcurrencyTests(TransactionTestCase):
    """Two connections doing the real thing at the same moment. Whatever interleaving happens, the invariant is
    the same: exactly one plot per canonical key, and the loser gets a clean answer (never a raw database error)."""

    def setUp(self):
        self.a = Company.objects.create(name="A Homes")
        self.mgr = User.objects.create_user("mgr_a", password="x", company=self.a, role="manager")
        self.estate = Estate.objects.create(company=self.a, name="Green Valley")

    def race(self, first, second):
        gate, results = threading.Barrier(2), {}

        def run(name, work):
            try:
                gate.wait(timeout=10)
                results[name] = work()
            except Exception as exc:                      # recorded, asserted on below
                results[name] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=("first", first)),
                   threading.Thread(target=run, args=("second", second))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def test_two_registrations_of_one_plot_in_different_cases(self):
        for round_ in range(6):
            number = f"R-{round_}"
            results = self.race(
                lambda: inv.register_plot(self.a, self.mgr, estate=self.estate, plot_number=number),
                lambda: inv.register_plot(self.a, self.mgr, estate=self.estate, plot_number=number.lower()))
            failures = [r for r in results.values() if isinstance(r, Exception)]
            self.assertEqual(len(results), 2)
            self.assertEqual([type(f) for f in failures], [inv.DuplicatePlot] * len(failures), results)
            self.assertEqual(Plot.objects.filter(estate=self.estate, plot_key=number.lower()).count(), 1)

    def test_a_registration_against_an_add_sale_for_the_same_plot(self):
        for round_ in range(6):
            number = f"S-{round_}"
            results = self.race(
                lambda: inv.register_plot(self.a, self.mgr, estate=self.estate, plot_number=number),
                lambda: services.add_sale(self.a, self.mgr, plot_number=number.lower(), price="1000",
                                          sale_date=date.today(), estate=self.estate, buyer_name=f"Buyer {number}"))
            failures = [r for r in results.values() if isinstance(r, Exception)]
            self.assertEqual([type(f) for f in failures], [inv.DuplicatePlot] * len(failures), results)
            self.assertEqual(Plot.objects.filter(estate=self.estate, plot_key=number.lower()).count(), 1)
            self.assertEqual(Sale.objects.filter(plot__plot_key=number.lower()).count(), 1)

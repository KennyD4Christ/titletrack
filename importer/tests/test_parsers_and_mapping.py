from django.test import SimpleTestCase, TestCase
from unittest.mock import patch

from importer import parsers
from importer.errors import ImportFileError, MappingError
from importer.mapping import (
    BLANK_KEY, distinct_status_values, normalize_status_value, suggest_mapping, suggest_status,
    validate_mapping, validate_status_mapping,
)
from importer.parsers import parse_upload
from importer.tests.helpers import D, HEADERS, MAPPING, make_batch, make_company_with_types, make_xlsx


class ParserTests(SimpleTestCase):
    def test_title_rows_above_the_header_are_skipped_and_row_numbers_are_sheet_rows(self):
        data = make_xlsx([["GV", 12.0, "Ada", 1000, 500, D(2026, 1, 5), None, None, None, None],
                          [None] * 10,
                          ["GV", 13, "Bayo", 1000, 0, "03/02/2026", None, None, None, None]],
                         title="Green Valley Sales Register")
        parsed = parse_upload("a.xlsx", data)
        self.assertEqual(parsed.header_row, 3)
        self.assertEqual(parsed.headers, HEADERS)
        self.assertEqual([r["n"] for r in parsed.rows], [4, 6])                   # blank row 5 skipped, numbering kept
        self.assertEqual(parsed.rows[0]["v"][1], 12)                              # 12.0 -> 12
        self.assertEqual(parsed.rows[0]["v"][5], "2026-01-05")                    # real dates become ISO text
        self.assertEqual(parsed.rows[1]["v"][5], "03/02/2026")                    # text dates stay text
        self.assertEqual(parsed.sheets, ["Sales", "Notes"])

    def test_sheet_choice_and_unknown_sheet(self):
        data = make_xlsx([["GV", 1, "A", 1, 1, None, None, None, None, None]])
        with self.assertRaises(ImportFileError):
            parse_upload("a.xlsx", data, sheet="Nope")
        with self.assertRaises(ImportFileError):
            parse_upload("a.xlsx", data, sheet="Notes")      # empty sheet: no header row

    def test_forced_header_row(self):
        data = make_xlsx([["GV", 1, "A", 1, 1, None, None, None, None, None]], title="Title")
        self.assertEqual(parse_upload("a.xlsx", data, header_row=3).headers, HEADERS)
        with self.assertRaises(ImportFileError):
            parse_upload("a.xlsx", data, header_row=500)

    def test_duplicate_and_empty_headers_are_made_unique(self):
        data = make_xlsx([["a", "b", "c", "d"]], headers=["Name", "Name", None, "Name"])
        self.assertEqual(parse_upload("a.xlsx", data).headers, ["Name", "Name (2)", "Column 3", "Name (3)"])

    def test_csv_with_bom_and_semicolons(self):
        content = "\ufeffEstate;Plot;Buyer\nGV;1;Ada\nGV;2;Bayo\n".encode("utf-8")
        parsed = parse_upload("a.csv", content)
        self.assertEqual(parsed.headers, ["Estate", "Plot", "Buyer"])
        self.assertEqual(len(parsed.rows), 2)

    def test_csv_legacy_encoding(self):
        content = "Estate,Plot,Buyer\nGV,1,Ol\xfa\n".encode("cp1252")
        self.assertEqual(parse_upload("a.csv", content).rows[0]["v"][2], "Ol\xfa")

    def test_rejections(self):
        for name, content in (("a.txt", b"x"), ("a.xls", b"x"), ("a.xlsx", b"not a zip"), ("a.csv", b""),
                              ("a.xlsx", b"")):
            with self.assertRaises(ImportFileError, msg=name):
                parse_upload(name, content)
        with self.assertRaises(ImportFileError):
            parse_upload("a.csv", b"Estate,Plot,Buyer\n")                         # header but no data

    def test_row_and_size_limits(self):
        rows = [["GV", i, "A", 1, 1, None, None, None, None, None] for i in range(10)]
        with patch.object(parsers, "MAX_ROWS", 5):
            with self.assertRaises(ImportFileError):
                parse_upload("a.xlsx", make_xlsx(rows))
        with patch.object(parsers, "MAX_BYTES", 10):
            with self.assertRaises(ImportFileError):
                parse_upload("a.xlsx", make_xlsx(rows))
        with patch.object(parsers, "MAX_COLS", 3):
            with self.assertRaises(ImportFileError):
                parse_upload("a.xlsx", make_xlsx(rows))


class MappingTests(TestCase):
    def test_suggest_mapping_from_common_headers(self):
        headers = ["Estate Name", "Plot Number", "Customer Name", "Selling Price", "Total Paid", "Date of Sale",
                   "Date Fully Paid", "Mobile", "Deed", "Survey Status"]
        m = suggest_mapping(headers, ["Deed of Assignment", "Survey Plan", "Allocation Letter"])
        self.assertEqual(m["estate"], "Estate Name")
        self.assertEqual(m["plot_number"], "Plot Number")
        self.assertEqual(m["buyer_name"], "Customer Name")
        self.assertEqual(m["price"], "Selling Price")
        self.assertEqual(m["amount_paid"], "Total Paid")
        self.assertEqual(m["sale_date"], "Date of Sale")
        self.assertEqual(m["fully_paid_on"], "Date Fully Paid")
        self.assertEqual(m["phone"], "Mobile")
        self.assertEqual(m["documents"], {"Deed of Assignment": "Deed", "Survey Plan": "Survey Status"})

    def test_a_header_is_never_suggested_twice(self):
        m = suggest_mapping(["Name", "Plot", "Estate", "Price", "Paid"], [])
        self.assertEqual(len(set(v for k, v in m.items() if k != "documents")), len([k for k in m if k != "documents"]))

    def test_validate_mapping_rules(self):
        types = {"Deed of Assignment", "Survey Plan"}
        self.assertEqual(validate_mapping(MAPPING, HEADERS, types)["estate"], "Estate")
        bad_cases = {
            "required missing": {k: v for k, v in MAPPING.items() if k != "price"},
            "unknown column": {**MAPPING, "price": "Cost"},
            "column reused": {**MAPPING, "amount_paid": "Price"},
            "unknown field": {**MAPPING, "colour": "Estate"},
            "unknown type": {**MAPPING, "documents": {"Made Up": "Deed Status"}},
            "doc column reused": {**MAPPING, "documents": {"Deed of Assignment": "Estate"}},
        }
        for label, mapping in bad_cases.items():
            with self.assertRaises(MappingError, msg=label):
                validate_mapping(mapping, HEADERS, types)
        with self.assertRaises(MappingError):
            validate_mapping("nonsense", HEADERS, types)

    def test_status_suggestions(self):
        expected = {
            "done": "completed", "yes": "completed", "issued": "completed", "n/a": "not_applicable",
            "pending": "not_started", BLANK_KEY: "not_started", "with lawyer": "awaiting_external",
            "submitted to registry": "awaiting_external", "in progress": "in_progress", "under review": "in_progress",
            "weird value": None,
        }
        for value, status in expected.items():
            self.assertEqual(suggest_status(normalize_status_value(value) if value != BLANK_KEY else BLANK_KEY), status, value)

    def test_status_value_normalisation(self):
        self.assertEqual(normalize_status_value("  With   LAWYER "), "with lawyer")
        self.assertEqual(normalize_status_value(None), BLANK_KEY)
        self.assertEqual(normalize_status_value("   "), BLANK_KEY)

    def test_validate_status_mapping(self):
        self.assertEqual(validate_status_mapping({" Done ": "completed"}), {"done": "completed"})
        with self.assertRaises(MappingError):
            validate_status_mapping({"done": "finished"})

    def test_distinct_status_values_counts_and_suggests(self):
        co, mgr = make_company_with_types()
        rows = [["GV", 1, "A", 1, 1, None, None, None, "Done", "Pending"],
                ["GV", 2, "B", 1, 1, None, None, None, "done ", None],
                ["GV", 3, "C", 1, 1, None, None, None, "Mystery", "Pending"]]
        batch = make_batch(co, mgr, rows)
        values = {v["key"]: v for v in distinct_status_values(batch)}
        self.assertEqual(values["done"]["count"], 2)
        self.assertEqual(values["done"]["suggested"], "completed")
        self.assertEqual(values["pending"]["count"], 2)
        self.assertEqual(values[BLANK_KEY]["count"], 1)
        self.assertIsNone(values["mystery"]["suggested"])

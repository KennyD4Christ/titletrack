import io
from datetime import datetime

import openpyxl

from importer.parsers import parse_upload
from tracker.models import Company, ImportBatch, ObligationType, User

HEADERS = ["Estate", "Plot No", "Buyer Name", "Price", "Amount Paid", "Sale Date", "Date Fully Paid",
           "Phone", "Deed Status", "Survey Status"]

MAPPING = {
    "estate": "Estate", "plot_number": "Plot No", "buyer_name": "Buyer Name", "price": "Price",
    "amount_paid": "Amount Paid", "sale_date": "Sale Date", "fully_paid_on": "Date Fully Paid", "phone": "Phone",
    "documents": {"Deed of Assignment": "Deed Status", "Survey Plan": "Survey Status"},
}
STATUS_MAPPING = {"done": "completed", "pending": "not_started", "(blank)": "not_started",
                  "with lawyer": "awaiting_external", "in progress": "in_progress"}

D = datetime  # shorthand for real Excel date cells


def make_xlsx(rows, headers=HEADERS, title=None, sheet="Sales", extra_sheet=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if title:
        ws.append([title])
        ws.append([])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    if extra_sheet:
        wb.create_sheet("Notes")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_batch(company, user, rows, mapping=None, status_mapping=None, status="mapped", filename="register.xlsx", **kw):
    parsed = parse_upload(filename, make_xlsx(rows, **kw))
    return ImportBatch.objects.create(
        company=company, created_by=user, filename=filename, sheet_name=parsed.sheet, sheets=parsed.sheets,
        file_sha256=parsed.sha256, headers=parsed.headers, rows=parsed.rows, row_count=len(parsed.rows),
        mapping=MAPPING if mapping is None else mapping,
        status_mapping=STATUS_MAPPING if status_mapping is None else status_mapping, status=status)


def make_company_with_types(name="A Homes"):
    co = Company.objects.create(name=name)
    manager = User.objects.create_user(f"mgr_{name[:1].lower()}", password="x", company=co, role="manager")
    ObligationType.objects.create(company=co, name="Allocation Letter", trigger="on_sale", sla_days=30)
    ObligationType.objects.create(company=co, name="Survey Plan", trigger="on_full_payment", sla_days=60)
    ObligationType.objects.create(company=co, name="Deed of Assignment", trigger="on_full_payment", sla_days=90)
    return co, manager

import csv
import io

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Stop spreadsheet formula injection: a cell that starts with = + - @ gets a leading apostrophe."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_DANGEROUS) else text


def issues_csv(batch):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["row", "level", "column", "value", "message"])
    for issue in (batch.report or {}).get("issues", []):
        writer.writerow([issue["row"], issue["level"], safe_cell(issue["column"]),
                         safe_cell(issue.get("value")), safe_cell(issue["message"])])
    return out.getvalue()

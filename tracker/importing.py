import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y")


def clean_amount(value):
    """'₦25,000,000' / 'N25,000,000.00' / 25000000 -> Decimal. Raises ValueError on junk."""
    if value is None:
        raise ValueError("amount is empty")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        raise ValueError("amount is empty")
    text = re.sub(r"^(₦|NGN|N)\s*", "", text, flags=re.IGNORECASE).replace(",", "").replace(" ", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"not a valid amount: {value!r}")


def clean_date(value):
    """Day-first dates (Nigerian convention). Raises ValueError if it cannot be read."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("date is empty")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a valid date: {value!r}")

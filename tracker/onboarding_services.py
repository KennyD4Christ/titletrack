"""Company onboarding, in one place.

The `onboard_company` command uses this today. The Django admin onboarding view (a later stage) will call the
same functions, so there is only ONE implementation of the rules.

Nothing here sends email or prints anything. The caller delivers the setup link AFTER `onboard_company()`
returns, because `account_services.deliver_link` must not run inside a transaction.

About the audit trail: `account_services.issue_link` refuses an actor from another company, so the first-link
event is always recorded without an actor. `actor` below is recorded on the "created" event only.
"""
import re
import zlib
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, connection, transaction

from tracker import account_services as acc
from tracker.models import AccountEvent, Company, Department, User

USERNAME_LIMIT = User._meta.get_field("username").max_length      # 150: the email becomes the username
NAME_LIMIT = User._meta.get_field("first_name").max_length


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


class OnboardingError(Exception):
    """The request breaks a rule. `field` names the input at fault (company, departments, md_email,
    md_first_name, md_last_name) so a form can show the message beside it. `code` is set only where a caller
    needs to tell one message apart from its neighbours."""

    def __init__(self, message, field=None, code=None):
        super().__init__(message)
        self.message, self.field, self.code = message, field, code


@dataclass(frozen=True)
class CleanOnboarding:
    name: str
    departments: list
    email: str
    first_name: str
    last_name: str


@dataclass
class OnboardingResult:
    company: object
    md: object
    departments: list
    issued: object          # IssuedLink: pass it to account_services.deliver_link() once, after this returns


def validate_onboarding(*, company, departments, md_email, md_first_name, md_last_name):
    """Normalise the input and enforce every rule. Raises OnboardingError at the first problem; returns the
    cleaned values. Reads the database (duplicate company name, duplicate email), writes nothing."""
    name = norm(company)
    if not name:
        raise OnboardingError("Company name is required.", "company")
    if len(name) > Company._meta.get_field("name").max_length:
        raise OnboardingError("Company name is too long.", "company")
    if name.casefold().startswith("demo"):
        raise OnboardingError("Company names starting with 'Demo' are reserved for demo data.", "company")
    if any(norm(existing).casefold() == name.casefold() for existing in Company.objects.values_list("name", flat=True)):
        raise OnboardingError(f"A company called '{name}' already exists.", "company")

    cleaned, seen = [], set()
    for raw in departments or []:
        d = norm(raw)
        if not d:
            raise OnboardingError("A department name is empty.", "departments", code="department_empty")
        if len(d) > Department._meta.get_field("name").max_length:
            raise OnboardingError(f"Department '{d}' is too long.", "departments")
        if d.casefold() in seen:
            raise OnboardingError(f"Department '{d}' is listed twice.", "departments")
        seen.add(d.casefold())
        cleaned.append(d)

    email = norm(md_email).lower()
    try:
        validate_email(email)
    except ValidationError:
        raise OnboardingError("The MD email address is not valid.", "md_email") from None
    if len(email) > USERNAME_LIMIT:
        raise OnboardingError(f"The MD email address is too long (maximum {USERNAME_LIMIT} characters).", "md_email")
    if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
        raise OnboardingError("That email address is already used by another account.", "md_email")

    first, last = norm(md_first_name), norm(md_last_name)
    for label, field, value in (("first name", "md_first_name", first), ("last name", "md_last_name", last)):
        if not value:
            raise OnboardingError(f"The MD {label} is required.", field)
        if len(value) > NAME_LIMIT:
            raise OnboardingError(f"The MD {label} is too long.", field)
    return CleanOnboarding(name, cleaned, email, first, last)


def _lock_company_name(name_key):
    """Make two onboardings of the same company name take turns (PostgreSQL advisory lock, released when the
    transaction ends). Company.name has no database uniqueness rule, so without this two simultaneous requests
    could both pass the duplicate check. Other databases skip it."""
    if connection.vendor != "postgresql":
        return
    key = zlib.crc32(f"titletrack.onboard-company:{name_key}".encode("utf-8"))
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def onboard_company(*, company, departments, md_email, md_first_name, md_last_name,
                    created_note, issued_note, actor=None):
    """Validate, then create the company, its departments, its first MD (no password) and the first setup link,
    all or nothing. Returns an OnboardingResult; the caller delivers `result.issued` afterwards.

    Raises OnboardingError for any broken rule, including a duplicate that a simultaneous request created
    after our check (the database's own uniqueness rules catch it, and we re-run the check to say which)."""
    departments = list(departments or [])
    inputs = dict(company=company, departments=departments, md_email=md_email,
                  md_first_name=md_first_name, md_last_name=md_last_name)
    try:
        with transaction.atomic():                      # all or nothing: company, departments, MD, first link
            name_key = norm(company).casefold()
            if name_key:
                _lock_company_name(name_key)
            data = validate_onboarding(**inputs)        # inside the lock, so the answer cannot go stale
            new_company = Company.objects.create(name=data.name)
            for d in data.departments:
                Department.objects.create(company=new_company, name=d)
            md = User(username=data.email, email=data.email, first_name=data.first_name,
                      last_name=data.last_name, company=new_company, role=User.Role.MD)
            md.set_unusable_password()
            md.save()
            acc.log_account_event(new_company.pk, AccountEvent.Action.CREATED, target_user=md, actor=actor,
                                  changes={"role": [None, "md"]}, note=created_note)
            issued = acc.issue_link(md, acc.SETUP, note=issued_note)      # never an actor: see module note
    except IntegrityError:
        validate_onboarding(**inputs)       # raises the precise OnboardingError if a rival request got there first
        raise                               # anything else is a genuine fault: let it surface
    return OnboardingResult(company=new_company, md=md, departments=data.departments, issued=issued)

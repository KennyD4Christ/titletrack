import re

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from tracker import account_services as acc
from tracker.models import AccountEvent, Company, Department, User

USERNAME_LIMIT = User._meta.get_field("username").max_length      # 150: the email becomes the username
NAME_LIMIT = User._meta.get_field("first_name").max_length


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


class Command(BaseCommand):
    help = (
        "Create a company, its departments and its first MD account (no password). Prints the one-time "
        "setup link unless email delivery is on and succeeds. Example:\n"
        "  python manage.py onboard_company --company \"Acme Estates\" --department Legal --department Survey "
        "--md-email md@acme.example --md-first-name Ada --md-last-name Obi"
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True)
        parser.add_argument("--department", action="append", default=[], help="Repeat for each department.")
        parser.add_argument("--md-email", required=True)
        parser.add_argument("--md-first-name", required=True)
        parser.add_argument("--md-last-name", required=True)
        parser.add_argument("--show-link-if-email-fails", action="store_true",
                            help="If email delivery is on but fails, print the link once instead of stopping.")

    def validate(self, opts):
        name = norm(opts["company"])
        if not name:
            raise CommandError("Company name is required.")
        if len(name) > Company._meta.get_field("name").max_length:
            raise CommandError("Company name is too long.")
        if name.casefold().startswith("demo"):
            raise CommandError("Company names starting with 'Demo' are reserved for demo data.")
        if any(norm(c.name).casefold() == name.casefold() for c in Company.objects.all()):
            raise CommandError(f"A company called '{name}' already exists.")

        departments, seen = [], set()
        for raw in opts["department"]:
            d = norm(raw)
            if not d:
                raise CommandError("A --department value is empty.")
            if len(d) > Department._meta.get_field("name").max_length:
                raise CommandError(f"Department '{d}' is too long.")
            if d.casefold() in seen:
                raise CommandError(f"Department '{d}' is listed twice.")
            seen.add(d.casefold())
            departments.append(d)

        email = norm(opts["md_email"]).lower()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("The MD email address is not valid.") from None
        if len(email) > USERNAME_LIMIT:
            raise CommandError(f"The MD email address is too long (maximum {USERNAME_LIMIT} characters).")
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise CommandError("That email address is already used by another account.")

        first, last = norm(opts["md_first_name"]), norm(opts["md_last_name"])
        for label, value in (("first name", first), ("last name", last)):
            if not value:
                raise CommandError(f"The MD {label} is required.")
            if len(value) > NAME_LIMIT:
                raise CommandError(f"The MD {label} is too long.")
        return name, departments, email, first, last

    def handle(self, *args, **opts):
        name, departments, email, first, last = self.validate(opts)
        with transaction.atomic():                      # all or nothing: company, departments, MD, first link
            company = Company.objects.create(name=name)
            for d in departments:
                Department.objects.create(company=company, name=d)
            md = User(username=email, email=email, first_name=first, last_name=last,
                      company=company, role=User.Role.MD)
            md.set_unusable_password()
            md.save()
            acc.log_account_event(company.pk, AccountEvent.Action.CREATED, target_user=md,
                                  changes={"role": [None, "md"]}, note="Created by the onboard_company command")
            issued = acc.issue_link(md, acc.SETUP, note="Issued by the onboard_company command")
        outcome = acc.deliver_link(issued, reveal_if_email_fails=opts["show_link_if_email_fails"])

        self.stdout.write(self.style.SUCCESS(
            f"Company '{name}' created with {len(departments)} department(s) and MD account {email}."))
        self.stdout.write("No document types were created.")
        report_outcome(self, outcome, "issue_setup_link --email " + email)


def report_outcome(command, outcome, retry_hint):
    days = "3 days"
    if outcome.status == "emailed":
        command.stdout.write(f"The setup link was emailed. It works once and expires in {days}.")
    elif outcome.status == "revealed":
        command.stdout.write(f"\nSETUP LINK (shown once, works once, expires in {days} at "
                             f"{outcome.expires_at:%Y-%m-%d %H:%M %Z}):\n{outcome.link}\n")
        command.stdout.write("Deliver it securely. Anyone who sees this link can use it, so do not paste it into chats "
                             "or tickets.")
    else:
        raise CommandError(
            "The email could not be sent (recorded in the account history). The account exists. "
            f"To get a link anyway, run: python manage.py {retry_hint} --show-link-if-email-fails")

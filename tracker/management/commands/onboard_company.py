from django.core.management.base import BaseCommand, CommandError

from tracker import account_services as acc
# norm and the two limits used to live in this module; keep them importable from here.
from tracker.onboarding_services import NAME_LIMIT, USERNAME_LIMIT, OnboardingError, norm, onboard_company  # noqa: F401

# The shared service words its messages for any caller (it also serves a web form); the command keeps its own
# command-line wording where that differs.
COMMAND_WORDING = {"department_empty": "A --department value is empty."}


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

    def handle(self, *args, **opts):
        try:
            result = onboard_company(
                company=opts["company"], departments=opts["department"], md_email=opts["md_email"],
                md_first_name=opts["md_first_name"], md_last_name=opts["md_last_name"],
                created_note="Created by the onboard_company command",
                issued_note="Issued by the onboard_company command")
        except OnboardingError as exc:
            raise CommandError(COMMAND_WORDING.get(exc.code, exc.message)) from None
        outcome = acc.deliver_link(result.issued, reveal_if_email_fails=opts["show_link_if_email_fails"])

        self.stdout.write(self.style.SUCCESS(
            f"Company '{result.company.name}' created with {len(result.departments)} department(s) "
            f"and MD account {result.md.email}."))
        self.stdout.write("No document types were created.")
        report_outcome(self, outcome, "issue_setup_link --email " + result.md.email)


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

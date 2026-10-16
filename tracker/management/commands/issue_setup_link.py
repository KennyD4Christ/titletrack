from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from tracker import account_services as acc
from tracker.management.commands.onboard_company import report_outcome
from tracker.models import User


class Command(BaseCommand):
    help = (
        "Issue a new setup link for a locked-out MD. Older links for that account stop working. Example:\n"
        "  python manage.py issue_setup_link --email md@acme.example"
    )

    def add_arguments(self, parser):
        who = parser.add_mutually_exclusive_group(required=True)
        who.add_argument("--email")
        who.add_argument("--username", help="For accounts that have no email address (for example demo accounts).")
        parser.add_argument("--show-link-if-email-fails", action="store_true",
                            help="If email delivery is on but fails, print the link once instead of stopping.")

    def handle(self, *args, **opts):
        if opts["email"]:
            matches = list(User.objects.filter(email__iexact=opts["email"].strip()))
        else:
            matches = list(User.objects.filter(username=opts["username"].strip()))
        if not matches:
            raise CommandError("No such account.")
        if len(matches) > 1:
            raise CommandError("More than one account has that email address. Use --username instead.")
        user = matches[0]
        if user.role != User.Role.MD:
            raise CommandError("This command is only for MD accounts.")
        try:
            issued = acc.issue_link(user, acc.SETUP, note="Issued by the issue_setup_link command")
        except (acc.AccountLinkError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from None
        outcome = acc.deliver_link(issued, reveal_if_email_fails=opts["show_link_if_email_fails"])
        report_outcome(self, outcome, "issue_setup_link " + (f"--email {opts['email']}" if opts["email"] else f"--username {opts['username']}"))

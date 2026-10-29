
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Temporarily bootstrap a platform superuser when explicitly enabled."

    def handle(self, *args, **options):
        if os.environ.get("TITLETRACK_BOOTSTRAP_SUPERUSER") != "1":
            self.stdout.write("Bootstrap disabled; no changes made.")
            return

        username = os.environ.get("TITLETRACK_BOOTSTRAP_USERNAME", "").strip()
        email = os.environ.get("TITLETRACK_BOOTSTRAP_EMAIL", "").strip()
        password = os.environ.get("TITLETRACK_BOOTSTRAP_PASSWORD", "")

        if not username or not email or not password:
            raise CommandError(
                "Bootstrap enabled, but required environment variables are missing."
            )

        User = get_user_model()

        existing_username = User.objects.filter(username=username).first()

        if existing_username:
            if (
                existing_username.is_superuser
                and existing_username.email.casefold() == email.casefold()
            ):
                self.stdout.write(
                    "Bootstrap account already exists; no changes made."
                )
                return

            raise CommandError(
                "That username belongs to a different account. "
                "No account or password was changed."
            )

        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(
                "That email already belongs to an account. No changes were made."
            )

        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Platform superuser {user.username!r} created successfully."
            )
        )

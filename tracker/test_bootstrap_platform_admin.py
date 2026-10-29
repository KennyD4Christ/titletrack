import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase


class BootstrapPlatformAdminTests(TestCase):
    def test_disabled_bootstrap_makes_no_changes(self):
        with patch.dict(os.environ, {"TITLETRACK_BOOTSTRAP_SUPERUSER": "0"}):
            call_command("bootstrap_platform_admin")

        self.assertEqual(get_user_model().objects.count(), 0)

    def test_creates_superuser_when_explicitly_enabled(self):
        env = {
            "TITLETRACK_BOOTSTRAP_SUPERUSER": "1",
            "TITLETRACK_BOOTSTRAP_USERNAME": "bootstrap_test_admin",
            "TITLETRACK_BOOTSTRAP_EMAIL": "bootstrap-test@example.invalid",
            "TITLETRACK_BOOTSTRAP_PASSWORD": "test-only-strong-password",
        }

        with patch.dict(os.environ, env):
            call_command("bootstrap_platform_admin")

        user = get_user_model().objects.get(username="bootstrap_test_admin")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password("test-only-strong-password"))

    def test_existing_username_is_not_modified(self):
        User = get_user_model()
        existing = User.objects.create_user(
            username="bootstrap_test_admin",
            password="original-test-password",
        )

        env = {
            "TITLETRACK_BOOTSTRAP_SUPERUSER": "1",
            "TITLETRACK_BOOTSTRAP_USERNAME": "bootstrap_test_admin",
            "TITLETRACK_BOOTSTRAP_EMAIL": "another-test@example.invalid",
            "TITLETRACK_BOOTSTRAP_PASSWORD": "replacement-test-password",
        }

        with patch.dict(os.environ, env):
            with self.assertRaises(CommandError):
                call_command("bootstrap_platform_admin")

        existing.refresh_from_db()
        self.assertTrue(existing.check_password("original-test-password"))
        self.assertFalse(existing.is_superuser)

    def test_existing_email_is_not_modified(self):
        User = get_user_model()
        existing = User.objects.create_user(
            username="existing_test_user",
            email="bootstrap-test@example.invalid",
            password="original-test-password",
        )

        env = {
            "TITLETRACK_BOOTSTRAP_SUPERUSER": "1",
            "TITLETRACK_BOOTSTRAP_USERNAME": "new_test_admin",
            "TITLETRACK_BOOTSTRAP_EMAIL": "bootstrap-test@example.invalid",
            "TITLETRACK_BOOTSTRAP_PASSWORD": "test-only-strong-password",
        }

        with patch.dict(os.environ, env):
            with self.assertRaises(CommandError):
                call_command("bootstrap_platform_admin")

        existing.refresh_from_db()
        self.assertTrue(existing.check_password("original-test-password"))
        self.assertFalse(
            User.objects.filter(username="new_test_admin").exists()
        )

    def test_existing_matching_superuser_is_left_unchanged(self):
        User = get_user_model()
        existing = User.objects.create_superuser(
            username="bootstrap_test_admin",
            email="bootstrap-test@example.invalid",
            password="original-test-password",
        )

        env = {
            "TITLETRACK_BOOTSTRAP_SUPERUSER": "1",
            "TITLETRACK_BOOTSTRAP_USERNAME": "bootstrap_test_admin",
            "TITLETRACK_BOOTSTRAP_EMAIL": "bootstrap-test@example.invalid",
            "TITLETRACK_BOOTSTRAP_PASSWORD": "replacement-test-password",
        }

        with patch.dict(os.environ, env):
            call_command("bootstrap_platform_admin")

        existing.refresh_from_db()
        self.assertTrue(existing.is_superuser)
        self.assertTrue(existing.check_password("original-test-password"))
        self.assertFalse(existing.check_password("replacement-test-password"))

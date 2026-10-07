import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from api.management.commands.export_permissions import DEFAULT_PATH, render
from api.permissions import MATRIX


class PermissionExportTests(SimpleTestCase):
    def test_export_round_trips_the_matrix(self):
        data = json.loads(render())
        self.assertEqual(set(data["actions"]), set(MATRIX))
        for action, roles in MATRIX.items():
            self.assertEqual(data["actions"][action], sorted(roles))

    def test_check_detects_a_stale_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.json"
            with self.assertRaises(CommandError):
                call_command("export_permissions", "--check", path=str(path), stdout=StringIO())   # missing file
            call_command("export_permissions", path=str(path), stdout=StringIO())
            call_command("export_permissions", "--check", path=str(path), stdout=StringIO())       # fresh file passes
            path.write_text(path.read_text().replace('"md"', '"md2"', 1))
            with self.assertRaises(CommandError):
                call_command("export_permissions", "--check", path=str(path), stdout=StringIO())

    def test_the_front_end_copy_is_up_to_date(self):
        """Fails when someone edits api/permissions.py without regenerating the React matrix."""
        if not DEFAULT_PATH.exists():
            self.skipTest("frontend/ not present")
        call_command("export_permissions", "--check", stdout=StringIO())

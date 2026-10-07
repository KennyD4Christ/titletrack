import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.permissions import ALL_ROLES, MATRIX

DEFAULT_PATH = Path(settings.BASE_DIR) / "frontend" / "src" / "utils" / "permissions.matrix.json"


def render():
    data = {
        "roles": sorted(ALL_ROLES),
        "actions": {action: sorted(roles) for action, roles in sorted(MATRIX.items())},
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


class Command(BaseCommand):
    help = "Write the role/action matrix for the React app (single source of truth: api/permissions.py)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(DEFAULT_PATH))
        parser.add_argument("--check", action="store_true", help="Fail if the file is out of date instead of writing it.")

    def handle(self, *args, **opts):
        path, text = Path(opts["path"]), render()
        if opts["check"]:
            if not path.exists() or path.read_text() != text:
                raise CommandError(f"{path} is out of date. Run: python manage.py export_permissions")
            self.stdout.write("Permission matrix is up to date.")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))

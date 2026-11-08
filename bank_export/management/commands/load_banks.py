from django.core.management.base import BaseCommand, CommandError

from bank_export import services


class Command(BaseCommand):
    help = "Load banks from a CSV/XLSX with CODE and NAME columns (e.g. docs/demiller/bank_codes.csv)."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, path, **opts):
        try:
            with open(path, "rb") as f:
                rows, errors = services.load_bank_rows(f, path)
        except (OSError, services.ExportError) as exc:
            raise CommandError(str(exc))
        for n, msg in errors:
            self.stderr.write(f"row {n}: {msg}")
        self.stdout.write(str(services.import_banks(rows)))

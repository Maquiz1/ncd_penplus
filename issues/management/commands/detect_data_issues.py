from django.core.management.base import BaseCommand

from issues.detectors import run_all


class Command(BaseCommand):
    help = 'Run the data-quality checks and add new issues to the Data issues list (safe to re-run).'

    def handle(self, *args, **options):
        for name, created in run_all().items():
            self.stdout.write(f'  {name}: {created} new')
        self.stdout.write(self.style.SUCCESS('Data-quality checks complete.'))

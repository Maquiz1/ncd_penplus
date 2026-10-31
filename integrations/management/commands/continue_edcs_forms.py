from django.core.management.base import BaseCommand

from integrations.legacy_forms import run


class Command(BaseCommand):
    help = ('Make the EDCS forms live (typed fields, data-dictionary labels, offered at visits) and map EDCS '
            'screening to pre-screening. Safe to run again; the EDCS import also does this.')

    def handle(self, *args, **options):
        report = run()
        self.stdout.write(f'Live forms: {", ".join(report["live_forms"]) or "none"}')
        if report['switched_off']:
            self.stdout.write(f'Switched off (unused, replaced): {", ".join(report["switched_off"])}')
        self.stdout.write(self.style.SUCCESS(f'{report["prescreenings"]} pre-screenings created from EDCS screening.'))

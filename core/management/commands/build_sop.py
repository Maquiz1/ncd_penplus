from django.core.management.base import BaseCommand

from core.sop import write


class Command(BaseCommand):
    help = ('Write documentations/sop/NCD_PENPlus_SOP_v<VERSION>.pdf from documentations/sop.md (VERSION and '
            'CHANGE_LOG are in core/sop.py). Commit it: issued versions stay downloadable on the SOP page.')

    def add_arguments(self, parser):
        parser.add_argument('--dir', default=None, help='Output folder (default: documentations/sop/).')

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f'Written {write(options["dir"])}'))

from django.core.management.base import BaseCommand

from exports.dictionary import write


class Command(BaseCommand):
    help = ('Write data_dictionary/NCD_PENPlus_Data_Dictionary_v<VERSION>.xlsx (VERSION and CHANGE_LOG are in '
            'exports/dictionary.py). Keep the earlier files: they stay downloadable on the Codebook page.')

    def add_arguments(self, parser):
        parser.add_argument('--dir', default=None, help='Output folder (default: data_dictionary/).')

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f'Written {write(options["dir"])}'))

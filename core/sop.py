"""The SOP (documentations/sop.md) shown in the system, downloadable as a versioned PDF.

To issue a new version: raise VERSION and VERSION_DATE, add a line to CHANGE_LOG, and run
    python manage.py build_sop   -> documentations/sop/NCD_PENPlus_SOP_v<VERSION>.pdf
Commit the PDF: every issued version stays downloadable on the SOP page.
"""
import re
from pathlib import Path

from django.conf import settings

VERSION = '1.0'
VERSION_DATE = '2026-11-09'

CHANGE_LOG = [
    ('1.0', '2026-11-09', 'First version: the subject flow (registration, screening, investigations, diagnosis, '
     'prescribing, admission, enrollment, follow-up schedules per programme), follow-up and unscheduled visits, '
     'outcomes, checks and corrections, and the EDCS forms and exports.'),
]

SOURCE = Path(settings.BASE_DIR) / 'documentations' / 'sop.md'
ARCHIVE_DIR = Path(settings.BASE_DIR) / 'documentations' / 'sop'
PREFIX = 'NCD_PENPlus_SOP_v'


def pdf_filename(version=VERSION):
    return f'{PREFIX}{version}.pdf'


def body_html():
    """The SOP as HTML, without its own top heading (the page shows the title and version)."""
    import markdown
    text = SOURCE.read_text(encoding='utf-8') if SOURCE.exists() else '_The SOP file is missing._'
    text = re.sub(r'\A# [^\n]*\n', '', text)
    return markdown.markdown(text, extensions=['tables', 'fenced_code', 'sane_lists', 'toc'])


def issued_versions():
    """[(version, path)] of the issued PDFs, newest first."""
    found = []
    for path in ARCHIVE_DIR.glob(f'{PREFIX}*.pdf'):
        version = path.stem[len(PREFIX):]
        if re.fullmatch(r'[0-9.]+', version):
            found.append((tuple(int(p) for p in version.split('.')), version, path))
    return [(v, p) for _key, v, p in sorted(found, reverse=True)]


def build_pdf():
    """The current SOP as PDF bytes (WeasyPrint)."""
    from django.template.loader import render_to_string
    from weasyprint import HTML
    html = render_to_string('core/sop_pdf.html', {'body': body_html(), 'version': VERSION,
                                                  'version_date': VERSION_DATE, 'change_log': CHANGE_LOG})
    return HTML(string=html).write_pdf()


def write(directory=None):
    directory = Path(directory or ARCHIVE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / pdf_filename()
    path.write_bytes(build_pdf())
    return path

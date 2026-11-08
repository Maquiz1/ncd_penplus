"""The versioned Excel data dictionary: every study form, dataset and variable, with its old EDCS name and codes.

Built from the same datasets as the exports and the codebook page, so it always matches what is exported.
    python manage.py build_dictionary   -> data_dictionary/NCD_PENPlus_Data_Dictionary_v<VERSION>.xlsx

Open questions are in exports/decisions.py. To issue a new version: raise VERSION, add a line to CHANGE_LOG, run build_dictionary and keep the older files in
data_dictionary/ (not in git). Every earlier version found there stays downloadable on the Codebook page.
"""
import re
from pathlib import Path

from django.conf import settings
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

VERSION = '0.1'
VERSION_DATE = '2026-11-08'

# Version history: add a line for every new version.
CHANGE_LOG = [
    ('0.1', '2026-11-08', 'First version, from the system as it is: registration, the one Screening form, diagnosis '
     '(Confirmed / Suspected / Ruled out), PEN-Plus enrollment, visits, vital signs, investigations, prescriptions, '
     'admissions, the follow-up schedule and the EDCS forms still filled (trimmed: no repeated diagnosis forms, no '
     'medicine rows in the treatment plan, lab values moved to Investigations). Old EDCS table and column names '
     'added for every variable EDCS had. Replaces the EDCS "PenPLus Data Dictionary v1_2024_03_17.docx".'),
]

PREFIX = 'NCD_PENPlus_Data_Dictionary_v'
ARCHIVE_DIR = Path(settings.BASE_DIR) / 'data_dictionary'
HEADER_FILL = PatternFill('solid', fgColor='1F3864')
HEADER_FONT = Font(bold=True, color='FFFFFF')
YELLOW = PatternFill('solid', fgColor='FFF2CC')
GREY = PatternFill('solid', fgColor='EDEDED')
# Decisions sheet: High = red, Medium = yellow, Low = green.
PRIORITY_FILLS = {'High': PatternFill('solid', fgColor='F8CBCB'), 'Medium': PatternFill('solid', fgColor='FFE699'),
                  'Low': PatternFill('solid', fgColor='D9EAD3')}
PRIORITY_FONTS = {'High': Font(bold=True, color='9C0006'), 'Medium': Font(bold=True, color='7F6000'),
                  'Low': Font(color='274E13')}
ID_FILL = PatternFill('solid', fgColor='FCE4D6')
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical='top')


def dictionary_filename(version=VERSION):
    return f'{PREFIX}{version}.xlsx'


def archived_versions():
    """[(version, path)] of earlier issued versions, newest first, without the current one."""
    found = []
    for path in ARCHIVE_DIR.glob(f'{PREFIX}*.xlsx'):
        version = path.stem[len(PREFIX):]
        if version != VERSION and re.fullmatch(r'[0-9.]+', version):
            found.append((tuple(int(p) for p in version.split('.')), version, path))
    return [(v, p) for _key, v, p in sorted(found, reverse=True)]


def _table(ws, headers, rows, widths, start_row=1):
    for i, h in enumerate(headers, 1):
        c = ws.cell(start_row, i, h)
        c.fill, c.font, c.border, c.alignment = HEADER_FILL, HEADER_FONT, BORDER, WRAP
    for r, row in enumerate(rows, start_row + 1):
        for i, value in enumerate(row, 1):
            c = ws.cell(r, i, value)
            c.border, c.alignment = BORDER, WRAP
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(start_row + 1, 1)
    if rows:
        ws.auto_filter.ref = f'A{start_row}:{get_column_letter(len(headers))}{start_row + len(rows)}'


def build_workbook():
    from . import datasets as ds
    from .decisions import DECISIONS
    from .edcs_names import edcs_name
    from .study_forms import study_forms

    datasets = ds.all_datasets()
    forms = study_forms()
    form_of = {}
    for f in forms:
        for d in f.datasets:
            form_of.setdefault(d.key, f.title)
    variables = sum(len(d.columns) for d in datasets)
    open_high = sum(1 for d in DECISIONS if d[1] == 'High' and d[8] == 'Open')
    wb = Workbook()

    # README --------------------------------------------------------------------------------------------------------
    ws = wb.active
    ws.title = 'README'
    lines = [
        ('NCD PEN-Plus clinic (PEN-Plus study): Data Dictionary', None),
        (f'Version {VERSION}, {VERSION_DATE}. {len(forms)} study forms, {len(datasets)} datasets, {variables} variables. '
         f'{open_high} High decisions open.',
         None),
        (None, None),
        ('Purpose', 'Every variable the system exports, with its label, type, unit, codes, whether it is required, '
                    'whether it is kept in de-identified exports and its old EDCS name. It is generated from the system '
                    '(the same datasets as the exports and the Codebook page), so it matches the exported files.'),
        ('Study forms', 'The forms in the order of the old EDCS visit. A form can hold several datasets (tables); they '
                        'link by patient_id (NCD number), old_id (EDCS client number) and encounter_id (the visit).'),
        ('EDCS names', 'Column "EDCS table.column" gives the old EDCS name. Exports can use these names instead '
                       '(Exports page: Variable names = Old EDCS names). Variables EDCS did not have are left blank '
                       'and keep their new name.'),
        ('Codes', 'Codes are the values in the "Codes" export; the "Labels" export shows the code labels. Yes/No '
                  'questions of this system are 1 = Yes, 0 = No; the EDCS forms keep their own codes.'),
        ('Identifying', 'Variables marked "No" under "In de-identified export" (names, phone numbers, identifiers, '
                        'addresses, free text) are only exported for authorised data management.'),
        ('Required', 'Yes: the system does not save the record without it. Blank: optional or not set by entry.'),
        ('Versions', f'Earlier versions are kept in data_dictionary/ and can be downloaded on the Codebook page. '
                     'Change Log lists what changed.'),
        (None, None),
        ('Sheets', None),
        ('Study Forms', 'The study forms, their old EDCS tables and their datasets.'),
        ('Datasets', 'One row per dataset (exported table).'),
        ('Data Dictionary', 'One row per variable.'),
        ('Choices', 'The codes and labels of every choice variable.'),
        ('Lab Tests', 'The laboratory catalogue: units and reference ranges.'),
        ('Decisions', 'Problems found, what the system does for now, and who decides. Answer in the yellow '
                      'columns, starting with the High items (red; Medium items are yellow, Low green).'),
        ('Change Log', 'What changed in each version.'),
    ]
    for r, (a, b) in enumerate(lines, 1):
        ws.cell(r, 1, a)
        ws.cell(r, 2, b).alignment = WRAP
        if a and b is not None:
            ws.cell(r, 1).font = Font(bold=True)
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'].font = Font(italic=True)
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 110

    # Study forms ---------------------------------------------------------------------------------------------------
    ws = wb.create_sheet('Study Forms')
    rows = [[n, f.title, f.old_tables, '\n'.join(f'{d.label} ({d.key})' for d in f.datasets), f.description]
            for n, f in enumerate(forms, 1)]
    _table(ws, ['#', 'Study form', 'Old EDCS table(s)', 'Datasets', 'Description'], rows, [5, 34, 30, 50, 70])

    # Datasets ------------------------------------------------------------------------------------------------------
    ws = wb.create_sheet('Datasets')
    rows = []
    for d in datasets:
        tables = {edcs_name(d, c)[0] for c in d.columns if edcs_name(d, c)}
        rows.append([d.key, d.label, d.group, form_of.get(d.key) or f'({d.group})', ', '.join(sorted(tables)), len(d.columns),
                     sum(1 for c in d.columns if not c.identifying), d.description])
    _table(ws, ['Dataset', 'Label', 'Group', 'Study form', 'EDCS table', 'Variables', 'In de-identified export',
                'Description'], rows, [30, 44, 22, 30, 24, 10, 12, 60])

    # Data dictionary -----------------------------------------------------------------------------------------------
    ws = wb.create_sheet('Data Dictionary')
    rows = []
    for d in datasets:
        for c in d.columns:
            old = edcs_name(d, c)
            codes = '; '.join(f'{code} = {label}' for code, label in c.choices)
            rows.append([len(rows) + 1, form_of.get(d.key) or f'({d.group})', d.key, c.name, f'{old[0]}.{old[1]}' if old else '',
                         c.label, c.kind, c.unit, codes, 'Yes' if c.required else '',
                         'No' if c.identifying else 'Yes'])
    _table(ws, ['Row', 'Study form', 'Dataset', 'Variable', 'EDCS table.column', 'Label', 'Type', 'Unit', 'Codes',
                'Required', 'In de-identified export'], rows, [6, 26, 26, 24, 28, 44, 10, 10, 50, 9, 12])
    ws.freeze_panes = 'E2'
    for r in range(2, len(rows) + 2):
        if ws.cell(r, 11).value == 'No':
            ws.cell(r, 11).fill = ID_FILL

    # Choices -------------------------------------------------------------------------------------------------------
    ws = wb.create_sheet('Choices')
    rows = [[d.key, c.name, code, label] for d in datasets for c in d.columns for code, label in c.choices]
    _table(ws, ['Dataset', 'Variable', 'Code', 'Label'], rows, [30, 28, 16, 60])

    # Lab tests -----------------------------------------------------------------------------------------------------
    from laboratory.models import LabTest
    ws = wb.create_sheet('Lab Tests')
    rows = []
    for t in LabTest.objects.select_related('category').order_by('category__order', 'order', 'name'):
        reference = t.reference_range or ' - '.join(f'{v:g}' for v in (t.reference_low, t.reference_high) if v is not None)
        rows.append([t.code, t.name, str(t.category or ''), t.specimen_type, t.get_result_type_display(), t.unit,
                     reference, 'Yes' if t.is_active else 'No'])
    _table(ws, ['Code', 'Test', 'Category', 'Specimen', 'Result type', 'Unit', 'Reference range', 'Active'], rows,
           [22, 40, 26, 14, 12, 12, 22, 8])

    # Decisions -----------------------------------------------------------------------------------------------------
    ws = wb.create_sheet('Decisions')
    ws.cell(1, 1, 'Answer in the yellow columns and change Status to Answered. Row 3 is an example only. IDs match '
                  'plan.md (D) and the laboratory design (L) and stay the same between versions.').font = Font(italic=True)
    rows = [list(d[:7]) + [d[7], '', d[8]] for d in DECISIONS]
    _table(ws, ['ID', 'Priority', 'Area', 'Form / item', 'Problem found', 'What the system does now / proposal',
                'Who decides', 'Answer', 'Answered by', 'Status'], rows, [6, 9, 18, 24, 60, 60, 20, 40, 18, 11],
           start_row=2)
    for r in range(3, len(rows) + 3):
        for col in (8, 9, 10):
            ws.cell(r, col).fill = YELLOW
        priority = ws.cell(r, 2).value
        if priority == 'Example':
            for col in range(1, 11):
                ws.cell(r, col).fill = GREY
        elif priority in PRIORITY_FILLS:
            ws.cell(r, 2).fill = PRIORITY_FILLS[priority]
            ws.cell(r, 2).font = PRIORITY_FONTS[priority]
            ws.cell(r, 1).font = PRIORITY_FONTS[priority]
    dv = DataValidation(type='list', formula1='"Open,Answered,Example"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'J3:J{len(rows) + 2}')

    # Change log ----------------------------------------------------------------------------------------------------
    ws = wb.create_sheet('Change Log')
    _table(ws, ['Version', 'Date', 'Changes'], [list(x) for x in CHANGE_LOG], [10, 12, 110])
    return wb


def write(directory=None):
    directory = Path(directory or ARCHIVE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / dictionary_filename()
    build_workbook().save(path)
    return path

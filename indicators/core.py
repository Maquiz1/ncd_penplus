"""PEN-Plus core quarterly indicators (Tanzania PEN-Plus Quarterly Reporting Form, Q4-2024 version).

Rows return patient sets so every number can be opened as a patient list. Stock rows (e.g. active patients)
are counted at the end of the quarter; flow rows (e.g. new enrollments) during the quarter.
"""
import datetime

from django.conf import settings
from django.utils import timezone

from .engine import age_on, quarter_bounds

STOCK, FLOW, PCT, COUNT = 'stock', 'flow', 'pct', 'count'


def quarter_rows(cohort, year, quarter):
    start, end = quarter_bounds(year, quarter)
    today = timezone.localdate()
    if start > today:
        return None
    as_of = min(end, today)
    conditions = sorted(cohort.conditions.values(), key=lambda c: (c.disease.order, c.order))
    rows = []

    def add(code, label, kind, value, indent=0, note=''):
        rows.append({'code': code, 'label': label, 'kind': kind, 'value': value, 'indent': indent, 'note': note})

    ever = cohort.ever_enrolled(as_of)
    active = cohort.active(as_of)
    add('PP0', 'Patients ever enrolled at PEN-Plus clinics', STOCK, ever)
    add('PP1', 'Patients active at PEN-Plus clinic', STOCK, active,
        note=f'Enrolled and not LTFU (no attended visit for {settings.LTFU_MONTHS} months), transferred out, withdrawn or dead.')
    for c in conditions:
        add(f'PP1.{c.code}', c.name, STOCK, cohort.with_condition(active, {c.code}, as_of), indent=1)
    add('PP2', 'Active patients < 18 years', STOCK, {p for p in active if age_on(cohort.patients[p].date_of_birth, as_of) < 18})
    add('PP2b', 'Active patients < 30 years', STOCK, {p for p in active if age_on(cohort.patients[p].date_of_birth, as_of) < 30},
        note='Q4-2024 reporting form uses < 30 years.')
    add('PP3', 'Active female patients', STOCK, {p for p in active if cohort.patients[p].sex == 'female'})
    since = as_of - datetime.timedelta(days=91)
    visits = cohort.encounter_dates()
    add('PP4', 'Active patients who had a visit within the last 3 months', STOCK,
        {p for p in active if any(since <= d <= as_of for d in visits.get(p, []))}, note='No longer required from Q4-2024.')
    new = {p for p in ever if start <= cohort.first_enrollment(p) <= as_of}
    add('PP5', 'New patients enrolled in the quarter', FLOW, new)
    for c in conditions:
        add(f'PP5.{c.code}', c.name, FLOW, cohort.with_condition(new, {c.code}, as_of), indent=1)
    ltfu = cohort.became('ltfu', start, as_of)
    add('PP6', 'Patients lost to follow up in the quarter', FLOW, ltfu)
    for c in conditions:
        add(f'PP6.{c.code}', c.name, FLOW, cohort.with_condition(ltfu, {c.code}, as_of), indent=1)
    add('PP7', 'Patient deaths ever', STOCK, cohort.died(datetime.date(1900, 1, 1), as_of) & ever)
    deaths = cohort.died(start, as_of) & ever
    add('PP8', 'Patient deaths in the quarter', FLOW, deaths)
    for c in conditions:
        add(f'PP8.{c.code}', c.name, FLOW, cohort.with_condition(deaths, {c.code}, as_of), indent=1)
    add('PP9', 'Patients transferred out in the quarter', FLOW, cohort.became('transferred_out', start, as_of))
    add('PP10', 'Patients hospitalized in the quarter', FLOW, cohort.hospitalized(start, as_of) & ever)
    tests, positive, scd_patients = cohort.scd_tests(start, as_of)
    add('PP11', 'Number of SCD tests', COUNT, (tests, scd_patients))
    add('PP12', 'Number of positive SCD tests', COUNT, (positive, scd_patients))
    echos, abnormal, echo_patients, abnormal_patients = cohort.echos(start, as_of)
    add('PP13', 'Number of echocardiograms', COUNT, (echos, echo_patients))
    add('PP14', 'Echocardiograms identifying pathology (RHD, congenital, heart failure…)', COUNT, (abnormal, abnormal_patients))

    def n(code):
        row = next(r for r in rows if r['code'] == code)
        return len(row['value']) if row['kind'] != COUNT else row['value'][0]

    def by_codes(prefix, codes):
        return sum(n(f'{prefix}.{c.code}') for c in conditions if c.code in codes)

    cardiac = {c.code for c in conditions if c.disease.code == 'cardiac'}
    calculated = [
        ('% of patients actively enrolled (active of ever enrolled)', n('PP1'), n('PP0')),
        ('% of diabetes patients with Type 1 (of Type 1 + Type 2)', by_codes('PP1', {'t1d'}), by_codes('PP1', {'t1d', 't2d'})),
        ('% of cardiac patients with RHD', by_codes('PP1', {'rhd'}), len(cohort.with_disease(active, 'cardiac', as_of))),
        ('% of active patients < 18 years', n('PP2'), n('PP1')),
        ('% of active patients < 30 years', n('PP2b'), n('PP1')),
        ('% of active patients who are female', n('PP3'), n('PP1')),
        ('% of active patients lost to follow up in the quarter', n('PP6'), n('PP1')),
        ('% of active patients who died in the quarter', n('PP8'), n('PP1')),
        ('% of active patients hospitalized in the quarter', n('PP10'), n('PP1')),
        ('% of SCD tests that were positive', n('PP12'), n('PP11')),
        ('% of echocardiograms identifying pathology', n('PP14'), n('PP13')),
    ]
    for index, (label, num, den) in enumerate(calculated, start=1):
        add(f'C{index}', label, PCT, (num, den))
    return rows


def year_table(cohort, year):
    """[(row template, [Q1..Q4 cell], YTD cell)] where a cell is {'n': …, 'pct': …} or None (future quarter)."""
    quarters = [quarter_rows(cohort, year, q) for q in (1, 2, 3, 4)]
    template = next(q for q in quarters if q is not None) if any(quarters) else []
    table = []
    for i, row in enumerate(template):
        cells = []
        for q in quarters:
            if q is None:
                cells.append(None)
                continue
            r = q[i]
            if r['kind'] == PCT:
                num, den = r['value']
                cells.append({'n': num, 'den': den, 'pct': round(100 * num / den, 1) if den else None})
            elif r['kind'] == COUNT:
                cells.append({'n': r['value'][0]})
            else:
                cells.append({'n': len(r['value'])})
        filled = [c for c in cells if c is not None]
        if not filled:
            ytd = None
        elif row['kind'] in (FLOW, COUNT):
            ytd = {'n': sum(c['n'] for c in filled)}
        else:
            ytd = filled[-1]
        table.append((row, cells, ytd))
    return table


def drill(cohort, year, quarter, code):
    rows = quarter_rows(cohort, year, quarter) or []
    row = next((r for r in rows if r['code'] == code), None)
    if row is None or row['kind'] == PCT:
        return None, set()
    return row, (row['value'][1] if row['kind'] == COUNT else row['value'])

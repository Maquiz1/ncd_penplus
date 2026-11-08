"""What the Investigations page offers for one subject: the test catalogue, what was already requested, and quick picks."""
from dataclasses import dataclass, field

from django.utils import timezone

from laboratory.models import LabOrder, LabPanel, LabTest, MonitoringRule
from laboratory.monitoring import DEFAULTS, monitoring_status

from .models import InvestigationOrder, InvestigationType

# Requests a programme repeats most often, shown first for subjects enrolled in it. Unknown codes are skipped.
QUICK_PICKS = {
    'diabetes': [('panel', 'dm-control'), ('panel', 'rft'), ('test', 'urine-alb'), ('test', 'lipids')],
    'cardiac': [('panel', 'heart-failure'), ('panel', 'anticoag'), ('type', 'ecg-12-lead'), ('type', 'echo')],
    'scd': [('panel', 'fbc'), ('test', 'hb-electro'), ('type', 'tcd')],
}
OTHER_GROUP = 'Other tests'


@dataclass
class Item:
    kind: str  # test, panel or type: the name of the form field is this plus "s"
    pk: int
    name: str
    hint: str = ''
    note: str = ''
    warn: bool = False
    tests: list = field(default_factory=list)

    @property
    def key(self):
        return f'{self.kind}-{self.pk}'

    @property
    def field_name(self):
        return f'{self.kind}s'


def orderable_tests():
    """Active tests; the "-legacy" ones only hold imported EDCS results where the old test was not specified."""
    return LabTest.objects.filter(is_active=True).exclude(code__endswith='-legacy').select_related('category')


def _date(value):
    return timezone.localtime(value).strftime('%d %b %Y')


def _open_note(when, today):
    if timezone.localdate(when) == today:
        return 'Already requested today'
    return f'Already requested {_date(when)}, waiting for result'


def history(patient, visit=None):
    """{'test': {pk: (note, warn)}, 'type': {pk: (note, warn)}}. warn: requesting it again would probably duplicate.

    Every visit makes its own requests: inside a visit only a request made in that same visit is a repeat. Requests
    of earlier visits (a baseline test still waiting for its result) are shown for information only.
    """
    today = timezone.localdate()
    tests, types = {}, {}

    def repeat(order, when, is_open):
        if visit is not None:
            return order.encounter_id == visit.pk
        return is_open or timezone.localdate(when) == today

    def note(order, when, is_open):
        if visit is not None:
            return 'Already requested in this visit' if order.encounter_id == visit.pk else \
                f'Requested {_date(when)} in an earlier visit, waiting for result'
        return _open_note(when, today)

    for o in (LabOrder.objects.filter(patient=patient).exclude(status='cancelled').order_by('ordered_at')):
        is_open = o.status in ('ordered', 'collected')
        if repeat(o, o.ordered_at, is_open):
            tests[o.test_id] = (note(o, o.ordered_at, is_open), True)
        elif o.test_id in tests and tests[o.test_id][1]:
            continue
        elif is_open:
            tests[o.test_id] = (note(o, o.ordered_at, is_open), False)
        else:
            result = f': {o.result_value} {o.result_unit}'.rstrip() if o.result_value else ''
            tests[o.test_id] = (f'Last result {_date(o.resulted_at or o.ordered_at)}{result}', False)
    for o in (InvestigationOrder.objects.filter(patient=patient, investigation_type__isnull=False)
              .exclude(status='cancelled').order_by('requested_at')):
        is_open = o.status in ('requested', 'scheduled')
        key = o.investigation_type_id
        if repeat(o, o.requested_at, is_open):
            types[key] = (note(o, o.requested_at, is_open), True)
        elif key in types and types[key][1]:
            continue
        elif is_open:
            types[key] = (note(o, o.requested_at, is_open), False)
        else:
            types[key] = (f'Last done {_date(o.performed_at or o.requested_at)}', False)
    return {'test': tests, 'type': types}


def _test_item(test, notes):
    note, warn = notes['test'].get(test.pk, ('', False))
    return Item('test', test.pk, test.name, test.specimen_type, note, warn)


def _panel_item(panel, notes):
    tests = [t for t in panel.tests.all() if t.is_active]
    repeats = [t.name for t in tests if notes['test'].get(t.pk, ('', False))[1]]
    note = f'Already requested: {", ".join(repeats)}' if repeats else ''
    return Item('panel', panel.pk, panel.name, ', '.join(t.name for t in tests), note, bool(repeats), [t.pk for t in tests])


def _type_item(itype, notes):
    note, warn = notes['type'].get(itype.pk, ('', False))
    return Item('type', itype.pk, itype.name, itype.preparation, note, warn)


def catalogue(form):
    """[(tab key, tab label, [(group label, [items])])], only with the groups that have something to request."""
    notes = form.notes
    lab, other = {}, []
    panels = [_panel_item(p, notes) for p in form.fields['panels'].queryset.prefetch_related('tests')]
    panels = [p for p in panels if p.tests]
    if panels:
        lab['Panels'] = panels
    for test in form.fields['tests'].queryset:
        if test.category_id:
            lab.setdefault(test.category.name, []).append(_test_item(test, notes))
        else:
            other.append(_test_item(test, notes))
    if other:
        lab[OTHER_GROUP] = other
    radiology = {}
    labels = dict(InvestigationType.Modality.choices)
    for modality in labels:
        items = [_type_item(t, notes) for t in form.fields['types'].queryset if t.modality == modality]
        if items:
            radiology[labels[modality]] = items
    tabs = [('lab', 'Laboratory', list(lab.items())), ('rad', 'Radiology', list(radiology.items()))]
    return [tab for tab in tabs if tab[2]]


def quick_picks(patient, form):
    """Due monitoring tests first, then the usual requests for the programmes the subject is in, being screened for or known to have."""
    notes = form.notes
    picks, seen = [], set()

    def add(item, label=None, badge=''):
        if item.key not in seen:
            seen.add(item.key)
            picks.append({'key': item.key, 'label': label or item.name, 'badge': badge})

    diseases = [*patient.programme_enrollments.filter(status='active').values_list('programme__disease__code', flat=True),
                *patient.screenings.filter(result='pending').values_list('disease__code', flat=True)]
    pre = patient.prescreenings.order_by('-date', '-pk').first()
    if pre and pre.known_ncd:
        diseases += pre.known_diseases.values_list('code', flat=True)
    panels = {p.code: p for p in form.fields['panels'].queryset.prefetch_related('tests')}
    known_tests = {t.code: t for t in form.fields['tests'].queryset}
    types = {t.code: t for t in form.fields['types'].queryset}
    for code in {*DEFAULTS, *MonitoringRule.objects.values_list('test__code', flat=True)}:
        status = monitoring_status(patient, code) if code in known_tests else None
        if status and status['state'] in ('overdue', 'this_month') and not status['pending']:
            add(_test_item(known_tests[code], notes), badge='Overdue' if status['overdue'] else 'Due')
    for disease in diseases:
        for kind, code in QUICK_PICKS.get(disease, []):
            if kind == 'panel' and code in panels:
                add(_panel_item(panels[code], notes))
            elif kind == 'test' and code in known_tests:
                add(_test_item(known_tests[code], notes))
            elif kind == 'type' and code in types:
                add(_type_item(types[code], notes))
    return picks


def preselected(params):
    """Tests to tick on arrival from a link, e.g. ?test=hba1c on the due list."""
    return {
        'tests': LabTest.objects.filter(is_active=True, code__in=params.getlist('test')),
        'panels': LabPanel.objects.filter(is_active=True, code__in=params.getlist('panel')),
        'types': InvestigationType.objects.filter(is_active=True, code__in=params.getlist('type')),
    }

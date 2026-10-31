"""Turn the EDCS forms into live forms of the new system, and map the EDCS screening table to pre-screening.

The EDCS import keeps every table without a structured equivalent as a read-only form (codes stored as text).
Here those forms get a new, typed version (dates, numbers, choice lists with the data-dictionary labels), all
old entries move to it, and the form is offered at the right visits, so staff carry on entering the same form.
Tables that already became structured records (labs, ECG/echo, admissions, medicines, outcomes…) stay read-only.

Safe to run again: forms already live are only re-checked, and screening rows already mapped are skipped.
"""
import datetime
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from crf.models import FormDefinition, FormField, FormSubmission, FormVersion
from crf.services import clone_version, publish_version

DICTIONARY = json.loads((Path(__file__).parent / 'edcs_dictionary.json').read_text())
LIVE_NOTE = 'Live version of the EDCS form'
EVERY_VISIT = ['baseline', 'follow_up', 'unscheduled']

# EDCS table: (name in the new system, stages, disease code or None, required at those visits)
LIVE_FORMS = {
    'symptoms': ('Symptoms & examination', EVERY_VISIT, None, True),
    'risks': ('Risks', EVERY_VISIT, None, True),
    'dgns_complctns_comorbdts': ('Complications & comorbidities', EVERY_VISIT, None, True),
    'treatment_plan': ('Treatment plan', EVERY_VISIT, None, True),
    'hospitalization': ('Hospitalization since the last visit', ['follow_up', 'unscheduled'], None, True),
    'history': ('Subject & family history', ['baseline'], None, True),
    'demographic': ('Demographics', ['baseline'], None, True),
    'social_economic': ('Socio-economic status', ['baseline'], None, True),
    'cardiac': ('Cardiac diagnosis', ['baseline'], 'cardiac', True),
    'diabetic': ('Diabetes diagnosis', ['baseline'], 'diabetes', True),
    'sickle_cell': ('Sickle cell diagnosis', ['baseline'], 'scd', True),
}
# Newer forms that never got an entry and would duplicate the live EDCS forms or a structured record.
REPLACED_FORMS = ['general-prescreening', 'vital-signs', 'cardiac-assessment', 'diabetes-assessment',
                  'scd-assessment', 'cv-risk']


def legacy_code(table):
    return f'edcs-{table.replace("_", "-")}'


def _is_number(value):
    try:
        Decimal(str(value))
        return True
    except (InvalidOperation, ValueError):
        return False


def _is_date(value):
    try:
        datetime.date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _typed(field, info, values):
    """Field type, options and label for an EDCS column, chosen so every stored value stays valid."""
    kind = (info.get('type') or '').lower()
    options = [(str(code), label) for code, label in info.get('options') or []]
    present = sorted({str(v) for v in values if v not in (None, '')})
    if options and kind != 'checkbox' and all(',' not in v for v in present):
        known = {code for code, _label in options}
        options += [(code, f'{code} (old EDCS code)') for code in present if code not in known]
        return FormField.FieldType.SELECT, '\n'.join(f'{code}|{label}' for code, label in options)
    if (kind == 'date' or 'date' in field.key) and all(_is_date(v) for v in present):
        return FormField.FieldType.DATE, ''
    if kind == 'number' and all(_is_number(v) for v in present):
        return FormField.FieldType.DECIMAL, ''
    if any(len(v) > 120 for v in present):
        return FormField.FieldType.TEXTAREA, ''
    return FormField.FieldType.TEXT, field.options


@transaction.atomic
def make_live(table):
    """Typed, published version of one EDCS form, with every old entry moved to it. Returns the definition."""
    name, stages, disease_code, required = LIVE_FORMS[table]
    definition = FormDefinition.objects.filter(code=legacy_code(table)).first()
    if definition is None:
        return None
    version = definition.published_version
    if version and not version.change_notes.startswith(LIVE_NOTE):
        submissions = FormSubmission.all_objects.filter(version__definition=definition)
        info = DICTIONARY.get(table, {})
        draft = clone_version(version, None)
        for field in FormField.objects.filter(section__version=draft):
            values = [s.get(field.key) for s in submissions.values_list('data', flat=True)]
            column = info.get(field.key, {})
            field.field_type, field.options = _typed(field, column, values)
            field.label = (column.get('label') or field.label)[:255]
            field.save()
        draft.change_notes = f'{LIVE_NOTE}: typed fields and data-dictionary labels; old entries moved here.'
        draft.save(update_fields=['change_notes'])
        version = publish_version(draft, None)
    if version:
        FormSubmission.all_objects.filter(version__definition=definition).exclude(version=version) \
            .update(version=version)
    definition.name = name
    definition.description = (f'PEN-Plus form, continued from the EDCS table "{table}". Entries from the EDCS '
                              'are included; answer codes keep their EDCS values.')
    definition.is_active, definition.stages, definition.is_required = True, stages, required
    definition.applicability = FormDefinition.Applicability.SINGLE if disease_code else FormDefinition.Applicability.UNIVERSAL
    definition.save()
    if disease_code:
        from clinical.models import Disease
        definition.diseases.set(Disease.objects.filter(code=disease_code))
    return definition


def retire_replaced_forms():
    """Switch off unused newer forms that the live EDCS forms (or structured records) replace."""
    switched = []
    for code in REPLACED_FORMS:
        definition = FormDefinition.objects.filter(code=code, is_active=True).first()
        if definition and not FormSubmission.all_objects.filter(version__definition=definition).exists():
            definition.is_active = False
            definition.save(update_fields=['is_active', 'updated_at'])
            switched.append(definition.name)
    return switched


# ------------------------------------------------------------------ EDCS screening → pre-screening

YES_NO = {'1': True, '2': False}
SCREENING_TYPE = {'1': 'facility', '2': 'community'}


def prescreening_from(submission):
    """Pre-screening values from one EDCS screening entry (codes from the PenPlus data dictionary)."""
    from clinical.models import PreScreening

    data = submission.data
    date = _date(data.get('screening_date')) or _date(data.get('conset_date')) or submission.patient.registration_date
    consent = YES_NO.get(data.get('consent'))
    eligible = data.get('eligibility')
    if consent is False:
        disposition = PreScreening.Disposition.NOT_NOW
    elif eligible == '2':
        disposition = PreScreening.Disposition.NOT_NOW
    else:
        disposition = PreScreening.Disposition.PROCEED
    notes = [data.get('remarks', '').strip()]
    if eligible not in ('1', '2'):
        notes.append('EDCS eligibility not recorded.')
    return {
        'date': date, 'screening_type': SCREENING_TYPE.get(data.get('screening_type'), 'facility'),
        'consent_obtained': consent, 'consent_date': _date(data.get('conset_date')) if consent else None,
        'resides_in_area': YES_NO.get(data.get('residence'), True), 'known_ncd': YES_NO.get(data.get('ncd')),
        'lab_test_requested': YES_NO.get(data.get('lab_request')),
        'lab_request_date': _date(data.get('lab_request_date')) if data.get('lab_request') == '1' else None,
        'disposition': disposition, 'notes': ' '.join(n for n in notes if n),
    }


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


@transaction.atomic
def map_screenings(user=None):
    """One pre-screening per EDCS screening entry; returns how many were created."""
    from clinical.models import PreScreening
    from integrations.edcs import SOURCE
    from integrations.models import ExternalRecord

    content_type = ContentType.objects.get_for_model(PreScreening)
    done = set(ExternalRecord.objects.filter(source=SOURCE, table='screening.prescreening', content_type=content_type)
               .values_list('source_id', flat=True))
    created = 0
    for submission in (FormSubmission.objects.filter(version__definition__code=legacy_code('screening'))
                       .select_related('patient', 'submitted_by')):
        if str(submission.pk) in done or submission.patient.is_deleted:
            continue
        prescreening = PreScreening.objects.create(
            patient=submission.patient, facility=submission.patient.registration_facility,
            created_by=user or submission.submitted_by, **prescreening_from(submission))
        ExternalRecord.objects.create(source=SOURCE, table='screening.prescreening', source_id=str(submission.pk),
                                      content_type=content_type, object_id=str(prescreening.pk),
                                      notes='From the EDCS screening form')
        created += 1
    return created


def run(user=None):
    """Everything above; returns a short report."""
    live = [d.name for d in (make_live(table) for table in LIVE_FORMS) if d]
    return {'live_forms': live, 'switched_off': retire_replaced_forms(), 'prescreenings': map_screenings(user)}

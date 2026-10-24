"""Exportable datasets: one per table / CRF form, limited to the user's facilities, with a matching codebook.

Every dataset starts with the same patient columns (patient_id, site, sex, age at the record date). Columns come
from the model fields (and, for CRF forms, from the form builder), so new fields appear without code changes.
De-identified exports drop names, phone numbers, identifiers, addresses, dates of birth and free-text notes.
"""
import datetime
import decimal
from dataclasses import dataclass, field

from django.db import models

from patients.models import IDENTITY_FIELDS

# Never exported: bookkeeping and staff columns.
SKIP_FIELDS = {'created_at', 'updated_at', 'created_by', 'is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason',
               'request_group', 'attachment'}
# Removed from de-identified exports in addition to IDENTITY_FIELDS (free text may name people).
FREE_TEXT = {'notes', 'clinical_notes', 'plan_notes', 'result_notes', 'discharge_summary', 'chief_complaint',
             'counselling_notes', 'instructions', 'cancel_reason', 'reason_for_change', 'comments'}
STAFF_FKS = {'created_by', 'clinician', 'staff', 'requested_by', 'reported_by', 'reviewed_by', 'ordered_by',
             'collected_by', 'resulted_by', 'prescriber', 'dispensed_by', 'screened_by', 'submitted_by', 'recorded_by',
             'performed_by', 'resolved_by', 'deleted_by', 'consent_obtained_by', 'assessed_by'}


@dataclass
class Column:
    name: str
    label: str
    kind: str                       # text, number, date, datetime, yes/no, choice, code
    getter: object                  # callable(obj, mode) -> value
    choices: list = field(default_factory=list)   # [(code, label)]
    unit: str = ''
    identifying: bool = False


@dataclass
class Dataset:
    key: str
    label: str
    group: str
    model: object
    patient_path: str = 'patient'
    date_field: str = ''
    columns: list = field(default_factory=list)
    queryset_filter: dict = field(default_factory=dict)
    select_related: tuple = ()
    description: str = ''


def _resolve(obj, path):
    for part in path.split('__'):
        if obj is None:
            return None
        obj = getattr(obj, part)
    return obj


def plain(value):
    if isinstance(value, datetime.datetime):
        from django.utils import timezone
        return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def natural_key(obj):
    if obj is None:
        return None
    for attr in ('code', 'patient_number', 'number'):
        value = getattr(obj, attr, None)
        if value not in (None, ''):
            return value
    return obj.pk


def patient_columns(patient_path, date_field):
    def patient(obj):
        return _resolve(obj, patient_path)

    def age(obj, mode):
        p = patient(obj)
        when = _resolve(obj, date_field) if date_field else None
        if p is None or p.date_of_birth is None:
            return None
        when = when.date() if isinstance(when, datetime.datetime) else (when or datetime.date.today())
        dob = p.date_of_birth
        return when.year - dob.year - ((when.month, when.day) < (dob.month, dob.day))

    return [
        Column('patient_id', 'Patient ID (NCD number)', 'text', lambda o, m: patient(o).patient_number),
        Column('old_id', 'Old EDCS ID', 'text', lambda o, m: patient(o).old_id),
        Column('site', 'Site (current facility code)', 'code', lambda o, m: patient(o).current_facility.code),
        Column('sex', 'Sex', 'choice', lambda o, m: patient(o).sex if m == 'codes' else patient(o).get_sex_display(),
               choices=[('male', 'Male'), ('female', 'Female')]),
        Column('age_years', 'Age at the record date (years)', 'number', age),
    ]


def field_columns(model, identity, skip=()):
    columns = []
    for f in model._meta.concrete_fields:
        if f.name in SKIP_FIELDS or f.name in skip or f.name in STAFF_FKS or f.name in ('patient',):
            continue
        name, label = f.name, str(f.verbose_name).capitalize()
        ident = f.name in identity or f.name in FREE_TEXT
        if f.primary_key:
            columns.append(Column('record_id', 'Record ID', 'number', lambda o, m: o.pk))
        elif f.is_relation:
            columns.append(Column(f'{name}_id' if name in ('encounter', 'appointment') else name, label, 'code',
                                  (lambda attr: lambda o, m: natural_key(getattr(o, attr)))(name), identifying=ident))
        elif f.choices:
            choices = [(str(v), str(lbl)) for v, lbl in f.flatchoices]
            columns.append(Column(name, label, 'choice', (lambda attr: lambda o, m: getattr(o, attr) if m == 'codes'
                                                          else getattr(o, f'get_{attr}_display')())(name),
                                  choices=choices, identifying=ident))
        elif isinstance(f, models.BooleanField):
            columns.append(Column(name, label, 'yes/no', (lambda attr: lambda o, m: yes_no(getattr(o, attr), m))(name),
                                  choices=[('1', 'Yes'), ('0', 'No')], identifying=ident))
        elif isinstance(f, models.JSONField):
            continue
        else:
            kind = ('datetime' if isinstance(f, models.DateTimeField) else 'date' if isinstance(f, models.DateField)
                    else 'number' if isinstance(f, (models.IntegerField, models.DecimalField, models.FloatField)) else 'text')
            columns.append(Column(name, label, kind, (lambda attr: lambda o, m: plain(getattr(o, attr)))(name),
                                  identifying=ident))
    return columns


def yes_no(value, mode):
    if value is None:
        return None
    if mode == 'codes':
        return 1 if value else 0
    return 'Yes' if value else 'No'


def model_dataset(key, label, group, model_label, patient_path='patient', date_field='', skip=(), extra=(),
                  description='', **kwargs):
    from django.apps import apps
    model = apps.get_model(model_label)
    identity = IDENTITY_FIELDS.get(model_label, set())
    columns = patient_columns(patient_path, date_field) if patient_path else []
    columns += field_columns(model, identity, skip) + list(extra)
    return Dataset(key, label, group, model, patient_path, date_field, columns, description=description, **kwargs)


def _patient_dataset():
    from patients.models import Patient
    identity = IDENTITY_FIELDS['patients.Patient']
    columns = [Column('patient_id', 'Patient ID (NCD number)', 'text', lambda o, m: o.patient_number),
               Column('age_years', 'Age today (years)', 'number', lambda o, m: o.age if o.date_of_birth else None)]
    columns += field_columns(Patient, identity, skip=('patient_number', 'merged_into', 'sms_consent_recorded_at'))
    return Dataset('patients', 'Patients (registration)', 'Registration & screening', Patient, '', 'registration_date',
                   columns, select_related=('current_facility', 'registration_facility', 'residence_ward', 'residence_street'))


def static_datasets():
    return [
        _patient_dataset(),
        model_dataset('screenings', 'Screenings', 'Registration & screening', 'clinical.Screening', date_field='screening_date',
                      select_related=('patient__current_facility', 'disease', 'facility')),
        model_dataset('family', 'Family members / siblings', 'Registration & screening', 'clinical.FamilyMember',
                      date_field='recorded_on', select_related=('patient__current_facility',)),
        model_dataset('diagnoses', 'Diagnoses (conditions & sub-types)', 'Care', 'clinical.Diagnosis',
                      date_field='diagnosis_date', select_related=('patient__current_facility', 'disease', 'condition'),
                      extra=[Column('main_condition', 'Main condition', 'code',
                                    lambda o, m: o.condition.root.code if o.condition_id else None)]),
        model_dataset('programme_enrollments', 'Clinical programme enrollments', 'Care', 'clinical.ProgrammeEnrollment',
                      date_field='enrollment_date', select_related=('patient__current_facility', 'programme', 'facility')),
        model_dataset('status_events', 'Programme status history (LTFU, returns, deaths …)', 'Care',
                      'clinical.EnrollmentEvent', patient_path='enrollment__patient', date_field='event_date',
                      select_related=('enrollment__patient__current_facility', 'enrollment__programme'),
                      extra=[Column('programme', 'Programme', 'code', lambda o, m: o.enrollment.programme.code)]),
        model_dataset('study_enrollments', 'Research study enrollments', 'Care', 'research.StudyEnrollment',
                      date_field='enrollment_date', select_related=('patient__current_facility', 'study', 'facility')),
        model_dataset('appointments', 'Appointments / visits', 'Care', 'encounters.Appointment', date_field='scheduled_date',
                      skip=('plan', 'plan_version', 'sequence', 'programme_enrollment', 'study_enrollment', 'clinic_session'),
                      select_related=('patient__current_facility', 'facility')),
        model_dataset('encounters', 'Encounters', 'Care', 'encounters.Encounter', date_field='encounter_date',
                      skip=('programme_enrollment', 'study_enrollment'),
                      select_related=('patient__current_facility', 'facility', 'appointment')),
        model_dataset('vitals', 'Vital signs', 'Care', 'encounters.Vitals', patient_path='encounter__patient',
                      date_field='taken_at', select_related=('encounter__patient__current_facility',)),
        model_dataset('admissions', 'Admissions / hospitalizations', 'Care', 'encounters.Admission',
                      date_field='admitted_at', skip=('readmission_of',),
                      select_related=('patient__current_facility', 'facility')),
        model_dataset('labs', 'Lab results', 'Services', 'laboratory.LabOrder', date_field='ordered_at', skip=('panel',),
                      select_related=('patient__current_facility', 'facility', 'test')),
        model_dataset('investigations', 'ECG / echo / imaging (summary)', 'Services', 'investigations.InvestigationOrder',
                      date_field='requested_at', select_related=('patient__current_facility', 'facility', 'investigation_type')),
        model_dataset('medication_history', 'Medication history', 'Services', 'pharmacy.MedicationRecord',
                      date_field='record_date', select_related=('patient__current_facility', 'medication')),
        model_dataset('prescriptions', 'Prescriptions', 'Services', 'pharmacy.Prescription', date_field='prescribed_at',
                      select_related=('patient__current_facility', 'facility', 'medication')),
    ]


def investigation_datasets():
    """One dataset per investigation type, with its structured report fields as columns."""
    from investigations.models import InvestigationOrder, InvestigationType
    result = []
    for itype in InvestigationType.objects.prefetch_related('fields'):
        columns = patient_columns('patient', 'performed_at') + [
            Column('record_id', 'Record ID', 'number', lambda o, m: o.pk),
            Column('performed_at', 'Performed', 'datetime', lambda o, m: plain(o.performed_at)),
            Column('status', 'Status', 'choice', lambda o, m: o.status if m == 'codes' else o.get_status_display(),
                   choices=[(str(v), str(lbl)) for v, lbl in InvestigationOrder.Status.choices]),
            Column('is_abnormal', 'Abnormal', 'yes/no', lambda o, m: yes_no(o.is_abnormal, m), choices=[('1', 'Yes'), ('0', 'No')]),
        ]
        for f in itype.fields.all():
            choices = [(c, c) for c in f.choice_list()]
            columns.append(Column(f.key, f.label, {'number': 'number', 'choice': 'choice'}.get(f.field_type, 'text'),
                                  (lambda key: lambda o, m: o.results.get(key))(f.key), choices=choices, unit=f.unit))
        columns += [Column('conclusion', 'Conclusion', 'text', lambda o, m: o.conclusion),
                    Column('findings', 'Findings (free text)', 'text', lambda o, m: o.findings, identifying=True)]
        result.append(Dataset(f'investigation-{itype.code}', itype.name, 'Investigation reports', InvestigationOrder,
                              'patient', 'requested_at', columns, queryset_filter={'investigation_type': itype},
                              select_related=('patient__current_facility',)))
    return result


def form_datasets():
    """One dataset per CRF form (including the migrated EDCS forms), columns from every version's fields."""
    from crf.models import FormDefinition, FormField, FormSubmission
    result = []
    for definition in FormDefinition.objects.order_by('code'):
        fields = {}
        for f in FormField.objects.filter(section__version__definition=definition).order_by('section__version__number',
                                                                                              'section__order', 'order'):
            fields[f.key] = f   # the latest version's definition of a key wins
        columns = patient_columns('patient', 'submitted_at') + [
            Column('record_id', 'Submission ID', 'number', lambda o, m: o.pk),
            Column('encounter_id', 'Encounter ID', 'code', lambda o, m: o.encounter_id),
            Column('form_version', 'Form version', 'number', lambda o, m: o.version.number),
            Column('status', 'Status', 'choice', lambda o, m: o.status if m == 'codes' else o.get_status_display(),
                   choices=[(str(v), str(lbl)) for v, lbl in FormSubmission.Status.choices]),
            Column('submitted_at', 'Submitted', 'datetime', lambda o, m: plain(o.submitted_at)),
        ]
        for key, f in fields.items():
            choices = f.choices()
            labels = dict(choices)

            def get(o, m, key=key, labels=labels):
                value = o.data.get(key)
                if m == 'labels' and labels:
                    if isinstance(value, list):
                        return '; '.join(labels.get(str(v), str(v)) for v in value)
                    return labels.get(str(value), value)
                return '; '.join(map(str, value)) if isinstance(value, list) else value
            kind = 'choice' if choices else {'number': 'number', 'decimal': 'number', 'integer': 'number',
                                             'date': 'date'}.get(f.field_type, 'text')
            columns.append(Column(key, f.label, kind, get, choices=choices, unit=f.unit,
                                  identifying=f.field_type in ('textarea',) or key in ('name', 'phone', 'remarks')))
        legacy = definition.code.startswith('edcs-')
        result.append(Dataset(f'form-{definition.code}', definition.name, 'Migrated EDCS forms' if legacy else 'CRF forms',
                              FormSubmission, 'patient', 'submitted_at', columns,
                              queryset_filter={'version__definition': definition},
                              select_related=('patient__current_facility', 'version')))
    return result


def all_datasets():
    return static_datasets() + investigation_datasets() + form_datasets()


def get_dataset(key):
    return next((d for d in all_datasets() if d.key == key), None)


def visible_columns(dataset, deidentified):
    return [c for c in dataset.columns if not (deidentified and c.identifying)]


def dataset_queryset(dataset, facilities, start=None, end=None):
    manager = getattr(dataset.model, 'objects')
    qs = manager.filter(**dataset.queryset_filter)
    site = f'{dataset.patient_path}__current_facility' if dataset.patient_path else 'current_facility'
    qs = qs.filter(**{f'{site}__in': facilities})
    if dataset.patient_path:
        qs = qs.filter(**{f'{dataset.patient_path}__is_deleted': False})
    if dataset.date_field and (start or end):
        is_datetime = isinstance(dataset.model._meta.get_field(dataset.date_field), models.DateTimeField)
        lookup = f'{dataset.date_field}__date' if is_datetime else dataset.date_field
        if start:
            qs = qs.filter(**{f'{lookup}__gte': start})
        if end:
            qs = qs.filter(**{f'{lookup}__lte': end})
    return qs.select_related(*dataset.select_related).order_by('pk')


def codebook_rows(datasets, deidentified):
    """(dataset, variable, label, type, unit, code, code label, in de-identified export) – one row per code."""
    for d in datasets:
        for c in d.columns:
            if deidentified and c.identifying:
                continue
            base = [d.label, c.name, c.label, c.kind, c.unit]
            flag = 'No' if c.identifying else 'Yes'
            if c.choices:
                for code, label in c.choices:
                    yield base + [code, label, flag]
            else:
                yield base + ['', '', flag]

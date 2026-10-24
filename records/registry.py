"""Every clinical record type staff can list, edit, soft-delete and (superusers) restore, in one place.

A record type names its model, how it reaches the patient, its date, the columns shown and how it is edited.
Deleting a record hides it everywhere (lists, reports, indicators); only a superuser can restore it.
"""
from dataclasses import dataclass, field

from django.apps import apps
from django.urls import reverse


@dataclass
class RecordType:
    key: str
    label: str
    group: str
    model_label: str
    date_field: str
    columns: list = field(default_factory=list)  # (heading, attribute path or callable)
    patient_path: str = 'patient'
    edit_url: str = ''           # URL name taking the record pk
    edit_fields: list = field(default_factory=list)  # generic edit form when there is no edit_url
    view_url: object = None      # callable(obj) -> URL; default: patient page
    form_definition: object = None  # CRF forms: one record type per form
    note: str = ''

    @property
    def model(self):
        return apps.get_model(self.model_label)

    @property
    def perm_prefix(self):
        return f'{self.model._meta.app_label}.%s_{self.model._meta.model_name}'

    def perm(self, action):
        return self.perm_prefix % action

    def manager(self, deleted=False):
        qs = self.model.all_objects.filter(is_deleted=deleted)
        if self.form_definition is not None:
            qs = qs.filter(version__definition=self.form_definition)
        return qs

    def patient_of(self, obj):
        for part in self.patient_path.split('__'):
            obj = getattr(obj, part)
        return obj

    def view(self, obj):
        if self.view_url:
            return self.view_url(obj)
        return self.patient_of(obj).get_absolute_url()

    def edit(self, obj):
        if self.edit_url:
            return reverse(self.edit_url, args=[obj.pk])
        if self.edit_fields:
            return reverse('records:edit', args=[self.key, obj.pk])
        return ''

    def cells(self, obj):
        values = []
        for _heading, attr in self.columns:
            if callable(attr):
                values.append(attr(obj))
                continue
            value = obj
            for part in attr.split('.'):
                value = getattr(value, part, None)
                if callable(value):
                    value = value()
                if value is None:
                    break
            values.append(value)
        return values


def _display(attr):
    return lambda obj: getattr(obj, f'get_{attr}_display')()


STATIC_TYPES = [
    RecordType('prescreening', 'Pre-screenings', 'Registration & screening', 'clinical.PreScreening', 'date',
               [('Facility', 'facility'), ('Disposition', _display('disposition'))],
               edit_fields=['date', 'resides_in_area', 'expected_stay_months', 'disposition', 'notes']),
    RecordType('screening', 'Screenings', 'Registration & screening', 'clinical.Screening', 'screening_date',
               [('Disease', 'disease'), ('Result', _display('result'))], edit_url='clinical:screening_update'),
    RecordType('family', 'Family members / siblings', 'Registration & screening', 'clinical.FamilyMember', 'recorded_on',
               [('Relation', _display('relation')), ('Age', 'age_years'), ('Sickle cell status', _display('scd_status'))],
               edit_url='clinical:family_edit'),
    RecordType('appointment', 'Appointments / visits', 'Care', 'encounters.Appointment', 'scheduled_date',
               [('Facility', 'facility'), ('Visit type', _display('stage')), ('Status', _display('status')),
                ('Origin', _display('origin'))], edit_url='encounters:appointment_update',
               note='Deleting removes a wrong appointment; use Cancel or Missed for a real appointment.'),
    RecordType('encounter', 'Encounters', 'Care', 'encounters.Encounter', 'encounter_date',
               [('Facility', 'facility'), ('Type', _display('stage')), ('Status', _display('status')),
                ('Scheduled', lambda e: 'Scheduled' if e.appointment_id else 'Unscheduled')],
               edit_url='encounters:encounter_update', view_url=lambda e: e.get_absolute_url(),
               note='Deleting an encounter also deletes its vitals and forms, and re-opens its appointment.'),
    RecordType('vitals', 'Vital signs', 'Care', 'encounters.Vitals', 'taken_at',
               [('BP', lambda v: f'{v.systolic_bp}/{v.diastolic_bp}' if v.systolic_bp else ''), ('Pulse', 'pulse'),
                ('Weight', 'weight_kg'), ('Glucose', 'blood_glucose')],
               patient_path='encounter__patient', view_url=lambda v: v.encounter.get_absolute_url(),
               edit_fields=['taken_at', 'weight_kg', 'height_cm', 'systolic_bp', 'diastolic_bp', 'pulse',
                            'temperature_c', 'respiratory_rate', 'spo2', 'blood_glucose', 'waist_cm', 'muac_cm',
                            'notes']),
    RecordType('diagnosis', 'Diagnoses', 'Care', 'clinical.Diagnosis', 'diagnosis_date',
               [('Disease', 'disease'), ('Condition', lambda d: d.condition.label if d.condition_id else d.description),
                ('Status', _display('status'))], edit_url='clinical:diagnosis_update'),
    RecordType('admission', 'Admissions / hospitalizations', 'Care', 'encounters.Admission', 'admitted_at',
               [('Facility', 'facility'), ('Reason', 'reason'), ('Discharged', 'discharged_at')],
               edit_fields=['ward', 'admitted_at', 'reason', 'discharged_at', 'discharge_outcome', 'discharge_summary']),
    RecordType('lab', 'Lab tests & results', 'Services', 'laboratory.LabOrder', 'ordered_at',
               [('Test', 'test.name'), ('Result', lambda o: f'{o.result_value} {o.result_unit}'.strip()),
                ('Status', _display('status'))],
               edit_fields=['result_value', 'result_notes', 'is_abnormal'],
               note='Editing corrects a result. Consumables already used are not returned to stock.'),
    RecordType('investigation', 'ECG / echo / imaging', 'Services', 'investigations.InvestigationOrder', 'requested_at',
               [('Investigation', 'name'), ('Status', _display('status')), ('Conclusion', 'conclusion')],
               edit_url='investigations:report', view_url=lambda i: reverse('investigations:detail', args=[i.pk])),
    RecordType('prescription', 'Prescriptions', 'Services', 'pharmacy.Prescription', 'prescribed_at',
               [('Medicine', 'medication'), ('Dose', 'dose'), ('Status', _display('status'))],
               note='Prescriptions with dispensing cannot be deleted; cancel them instead.'),
    RecordType('medication-history', 'Medication history', 'Services', 'pharmacy.MedicationRecord', 'record_date',
               [('Medicine', 'medication_name'), ('Action', _display('action')), ('Dose', 'dose')],
               edit_fields=['medication_name', 'action', 'dose', 'start_date', 'end_date', 'record_date', 'notes']),
]


def form_types():
    from crf.models import FormDefinition
    types = []
    for definition in FormDefinition.objects.order_by('code'):
        legacy = definition.code.startswith('edcs-')
        types.append(RecordType(
            f'form-{definition.pk}', definition.name, 'Migrated EDCS forms' if legacy else 'CRF forms',
            'crf.FormSubmission', 'created_at',
            [('Version', 'version.number'), ('Status', _display('status')), ('Encounter', 'encounter')],
            edit_url='' if legacy else 'crf:submission_update',
            view_url=lambda s: reverse('crf:submission_detail', args=[s.pk]), form_definition=definition))
    return types


def record_types():
    return STATIC_TYPES + form_types()


def get_type(key):
    return next((t for t in record_types() if t.key == key), None)


def type_for(obj):
    """The record type of an object (CRF submissions map to their form)."""
    label = obj._meta.label
    if label == 'crf.FormSubmission':
        return get_type(f'form-{obj.version.definition_id}')
    return next(t for t in STATIC_TYPES if t.model_label == label)

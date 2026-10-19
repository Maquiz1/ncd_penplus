import datetime
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.forms import BootstrapForm, apply_bootstrap

from .models import FormDefinition, FormField, FormSubmission, FormVersion


def build_django_field(field):
    common = {'label': field.label, 'required': False, 'help_text': field.help_text}
    t = field.field_type
    if t == FormField.FieldType.TEXTAREA:
        return forms.CharField(widget=forms.Textarea, **common)
    if t == FormField.FieldType.INTEGER:
        return forms.IntegerField(**common)
    if t == FormField.FieldType.DECIMAL:
        return forms.DecimalField(**common)
    if t == FormField.FieldType.DATE:
        return forms.DateField(**common)
    if t == FormField.FieldType.DATETIME:
        return forms.DateTimeField(**common)
    if t == FormField.FieldType.SELECT:
        return forms.ChoiceField(choices=[('', '---------')] + field.choices(), **common)
    if t == FormField.FieldType.MULTISELECT:
        return forms.MultipleChoiceField(choices=field.choices(), widget=forms.CheckboxSelectMultiple, **common)
    if t == FormField.FieldType.YESNO:
        return forms.ChoiceField(choices=[('', '---------'), ('yes', 'Yes'), ('no', 'No')], **common)
    return forms.CharField(max_length=500, **common)


def is_visible(field, values):
    """Conditional display: a simple equality rule, never executable code."""
    if not field.show_if_key:
        return True
    answer = values.get(field.show_if_key)
    if isinstance(answer, list):
        return field.show_if_value in answer
    return str(answer if answer is not None else '') == field.show_if_value


def serialize(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


class DynamicCRFForm(BootstrapForm):
    """A Django form built from a published FormVersion and validated server-side."""

    def __init__(self, *args, version, final=True, **kwargs):
        self.version = version
        self.final = final
        self.form_fields = list(version.fields())
        super().__init__(*args, **kwargs)
        for spec in self.form_fields:
            django_field = build_django_field(spec)
            if spec.unit:
                django_field.label = f'{spec.label} ({spec.unit})'
            django_field.spec = spec
            self.fields[spec.key] = django_field
        apply_bootstrap(self.fields)

    def sections(self):
        grouped = {}
        for spec in self.form_fields:
            grouped.setdefault(spec.section, []).append(self[spec.key])
        return grouped.items()

    def clean(self):
        cleaned = super().clean()
        for spec in self.form_fields:
            value = cleaned.get(spec.key)
            if not is_visible(spec, cleaned):
                cleaned[spec.key] = None
                continue
            empty = value in (None, '', [])
            if spec.required and empty and self.final:
                self.add_error(spec.key, 'This field is required.')
            if not empty and spec.field_type in ('integer', 'decimal'):
                if spec.min_value is not None and Decimal(value) < spec.min_value:
                    self.add_error(spec.key, f'Must be at least {spec.min_value.normalize()}.')
                if spec.max_value is not None and Decimal(value) > spec.max_value:
                    self.add_error(spec.key, f'Must be at most {spec.max_value.normalize()}.')
        return cleaned

    def answers(self):
        return {spec.key: serialize(self.cleaned_data.get(spec.key)) for spec in self.form_fields}


def patient_disease_ids(patient):
    ids = set(patient.screenings.values_list('disease_id', flat=True))
    ids |= set(patient.diagnoses.exclude(status='ruled_out').values_list('disease_id', flat=True))
    ids |= set(patient.programme_enrollments.filter(status__in=['pending', 'active'])
               .values_list('programme__disease_id', flat=True))
    return ids


def applicable_versions(patient, stage=None, location=None):
    """Latest published version of every form that applies to this patient (and encounter context)."""
    disease_ids = patient_disease_ids(patient)
    study_ids = set(patient.study_enrollments.filter(status__in=['pending', 'active']).values_list('study_id', flat=True))
    study_ids |= set(patient.consents.filter(decision='consented').values_list('study_id', flat=True))
    definitions = FormDefinition.objects.filter(is_active=True).filter(
        Q(applicability='universal')
        | Q(applicability__in=['selected', 'single'], diseases__in=disease_ids)
        | Q(applicability='study', study_id__in=study_ids)
    ).distinct().prefetch_related('versions')
    result = []
    for definition in definitions:
        if stage and definition.stages and stage not in definition.stages:
            continue
        if location and location not in definition.effective_locations():
            continue
        version = definition.published_version
        if version:
            result.append(version)
    return result


def encounter_form_status(encounter):
    """Required/optional forms for an encounter and whether each has been completed."""
    rows = []
    for version in applicable_versions(encounter.patient, encounter.stage, encounter.location_type):
        submissions = list(encounter.form_submissions.filter(version__definition=version.definition)
                           .select_related('version'))
        done = [s for s in submissions if s.status not in ('in_progress', 'returned')]
        rows.append({'version': version, 'definition': version.definition, 'submissions': submissions,
                     'complete': bool(done), 'required': version.definition.is_required})
    return rows


def clone_version(version, user):
    """Start a new draft version by copying sections and fields of an existing version."""
    definition = version.definition
    number = (definition.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
    new = FormVersion.objects.create(definition=definition, number=number, created_by=user,
                                     change_notes=f'Copied from v{version.number}')
    for section in version.sections.all():
        fields = list(section.fields.all())
        section.pk = None
        section.version = new
        section.save()
        for field in fields:
            field.pk = None
            field.section = section
            field.save()
    return new


def publish_version(version, user):
    if not version.fields().exists():
        raise ValueError('Add at least one field before publishing.')
    version.definition.versions.filter(status=FormVersion.Status.PUBLISHED).update(status=FormVersion.Status.RETIRED)
    version.status = FormVersion.Status.PUBLISHED
    version.published_at = timezone.now()
    version.published_by = user
    version.save()
    return version


def mark_submitted(submission, user):
    submission.status = FormSubmission.Status.SUBMITTED
    submission.submitted_by = user
    submission.submitted_at = timezone.now()

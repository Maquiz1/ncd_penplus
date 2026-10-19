from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from clinical.models import Disease
from core.models import LocationType, TimeStampedModel, WorkflowStage
from patients.models import Patient


class FormDefinition(TimeStampedModel):
    class Applicability(models.TextChoices):
        UNIVERSAL = 'universal', 'Universal (all diseases)'
        SELECTED = 'selected', 'Selected diseases'
        SINGLE = 'single', 'Single disease'
        STUDY = 'study', 'Study-specific'

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    applicability = models.CharField(max_length=20, choices=Applicability.choices, default=Applicability.UNIVERSAL)
    diseases = models.ManyToManyField(Disease, blank=True, related_name='form_definitions')
    study = models.ForeignKey('research.Study', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='form_definitions')
    stages = models.JSONField(default=list, blank=True, help_text='Workflow stages where the form applies (empty = any).')
    locations = models.JSONField(default=list, blank=True, help_text='Encounter locations where the form applies (empty = facility only).')
    is_required = models.BooleanField('Required at applicable encounters', default=True)
    is_repeatable = models.BooleanField('Can be completed more than once per encounter', default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('crf:definition_detail', args=[self.pk])

    @property
    def published_version(self):
        return self.versions.filter(status=FormVersion.Status.PUBLISHED).order_by('-number').first()

    @property
    def draft_version(self):
        return self.versions.filter(status=FormVersion.Status.DRAFT).order_by('-number').first()

    def effective_locations(self):
        return self.locations or [LocationType.FACILITY]

    def stage_labels(self):
        labels = dict(WorkflowStage.choices)
        return [labels.get(s, s) for s in self.stages] or ['Any stage']

    def location_labels(self):
        labels = dict(LocationType.choices)
        return [labels.get(s, s) for s in self.effective_locations()]


class FormVersion(TimeStampedModel):
    """Immutable once published: changes require a new version."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        RETIRED = 'retired', 'Retired'

    definition = models.ForeignKey(FormDefinition, on_delete=models.PROTECT, related_name='versions')
    number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    change_notes = models.TextField(blank=True)

    class Meta:
        ordering = ['definition', '-number']
        constraints = [models.UniqueConstraint(fields=['definition', 'number'], name='unique_form_version_number')]

    def __str__(self):
        return f'{self.definition.name} v{self.number}'

    def get_absolute_url(self):
        return reverse('crf:version_detail', args=[self.pk])

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def fields(self):
        return FormField.objects.filter(section__version=self).select_related('section').order_by('section__order', 'order', 'pk')


class ImmutableWhenPublishedMixin:
    def _version(self):
        raise NotImplementedError

    def save(self, *args, **kwargs):
        if not self._version().is_editable:
            raise ValidationError('Published or retired form versions cannot be changed. Create a new version.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self._version().is_editable:
            raise ValidationError('Published or retired form versions cannot be changed. Create a new version.')
        return super().delete(*args, **kwargs)


class FormSection(ImmutableWhenPublishedMixin, models.Model):
    version = models.ForeignKey(FormVersion, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=150)
    order = models.PositiveSmallIntegerField(default=1)
    help_text = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return self.title

    def _version(self):
        return self.version


class FormField(ImmutableWhenPublishedMixin, models.Model):
    class FieldType(models.TextChoices):
        TEXT = 'text', 'Short text'
        TEXTAREA = 'textarea', 'Long text'
        INTEGER = 'integer', 'Whole number'
        DECIMAL = 'decimal', 'Decimal number'
        DATE = 'date', 'Date'
        DATETIME = 'datetime', 'Date and time'
        SELECT = 'select', 'Single choice'
        MULTISELECT = 'multiselect', 'Multiple choice'
        YESNO = 'yesno', 'Yes / No'

    section = models.ForeignKey(FormSection, on_delete=models.CASCADE, related_name='fields')
    key = models.SlugField('Variable name', max_length=60, help_text='Stable key used in data exports, e.g. sbp_1.')
    label = models.CharField(max_length=255)
    field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    required = models.BooleanField(default=False)
    options = models.TextField(blank=True, help_text='For choice fields: one option per line, as "value|Label" or just "Label".')
    min_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    help_text = models.CharField(max_length=255, blank=True)
    show_if_key = models.SlugField('Show only if field', max_length=60, blank=True,
                                   help_text='Variable name of another field in this form.')
    show_if_value = models.CharField('…has value', max_length=100, blank=True)
    order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return f'{self.key}: {self.label}'

    def _version(self):
        return self.section.version

    def choices(self):
        result = []
        for line in self.options.splitlines():
            line = line.strip()
            if not line:
                continue
            value, _, label = line.partition('|')
            result.append((value.strip(), (label or value).strip()))
        return result

    def clean(self):
        if self.field_type in ('select', 'multiselect') and not self.choices():
            raise ValidationError({'options': 'Choice fields need at least one option.'})
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValidationError({'max_value': 'Maximum must be greater than minimum.'})
        if self.section_id:
            clash = FormField.objects.filter(section__version=self.section.version, key=self.key).exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError({'key': 'This variable name is already used in this form version.'})
            if self.show_if_key and self.show_if_key == self.key:
                raise ValidationError({'show_if_key': 'A field cannot depend on itself.'})


class FormSubmission(TimeStampedModel):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'In progress (draft)'
        SUBMITTED = 'submitted', 'Submitted'
        UNDER_REVIEW = 'under_review', 'Under review'
        VERIFIED = 'verified', 'Verified'
        RETURNED = 'returned', 'Returned for correction'
        NOT_APPLICABLE = 'not_applicable', 'Not applicable'

    version = models.ForeignKey(FormVersion, on_delete=models.PROTECT, related_name='submissions')
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='form_submissions')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='form_submissions')
    programme_enrollment = models.ForeignKey('clinical.ProgrammeEnrollment', on_delete=models.PROTECT, null=True,
                                             blank=True, related_name='form_submissions')
    study_enrollment = models.ForeignKey('research.StudyEnrollment', on_delete=models.PROTECT, null=True, blank=True,
                                         related_name='form_submissions')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    data = models.JSONField(default=dict, blank=True)
    not_applicable_reason = models.CharField(max_length=255, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('review_formsubmission', 'Can review / verify form submissions'),
                       ('publish_formversion', 'Can publish form versions')]

    def __str__(self):
        return f'{self.version} – {self.patient}'

    def get_absolute_url(self):
        return reverse('crf:submission_detail', args=[self.pk])

    @property
    def is_editable(self):
        return self.status in (self.Status.IN_PROGRESS, self.Status.RETURNED)

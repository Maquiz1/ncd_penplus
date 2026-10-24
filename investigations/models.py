from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from core.models import SoftDeleteModel
from facilities.models import Facility
from patients.models import Patient


class InvestigationType(models.Model):
    """Catalogue entry: one ECG, echo, imaging or other procedure staff can request, with its report fields."""

    class Modality(models.TextChoices):
        ECG = 'ecg', 'ECG'
        ECHO = 'echo', 'Echocardiography'
        XRAY = 'xray', 'X-ray'
        ULTRASOUND = 'ultrasound', 'Ultrasound'
        CT = 'ct', 'CT scan'
        TCD = 'tcd', 'Transcranial doppler (TCD)'
        OTHER = 'other', 'Other'

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=150)
    modality = models.CharField(max_length=20, choices=Modality.choices)
    description = models.CharField(max_length=255, blank=True)
    preparation = models.CharField('Patient preparation', max_length=255, blank=True)
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class InvestigationField(models.Model):
    """A structured item in an investigation report, e.g. Ejection fraction (%), Rhythm, RHD (WHF criteria)."""

    class FieldType(models.TextChoices):
        NUMBER = 'number', 'Number'
        CHOICE = 'choice', 'Choice'
        TEXT = 'text', 'Short text'

    investigation_type = models.ForeignKey(InvestigationType, on_delete=models.CASCADE, related_name='fields')
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=150)
    field_type = models.CharField(max_length=10, choices=FieldType.choices, default=FieldType.CHOICE)
    unit = models.CharField(max_length=30, blank=True)
    reference_low = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reference_high = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    choices = models.TextField(blank=True, help_text='One option per line; prefix abnormal options with "!".')
    required = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'pk']
        constraints = [models.UniqueConstraint(fields=['investigation_type', 'key'], name='unique_investigation_field_key')]

    def __str__(self):
        return f'{self.label} ({self.unit})' if self.unit else self.label

    def choice_list(self):
        return [line.strip().lstrip('!').strip() for line in self.choices.splitlines() if line.strip()]

    def abnormal_choices(self):
        return {line.strip()[1:].strip() for line in self.choices.splitlines() if line.strip().startswith('!')}

    def is_abnormal(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return False
        if self.field_type == self.FieldType.NUMBER:
            try:
                value = Decimal(raw.replace(',', '.'))
            except (InvalidOperation, ValueError):
                return False
            return ((self.reference_low is not None and value < self.reference_low)
                    or (self.reference_high is not None and value > self.reference_high))
        return self.field_type == self.FieldType.CHOICE and raw in self.abnormal_choices()


class InvestigationOrder(SoftDeleteModel):
    """ECG, echocardiography, imaging and other procedure requests and reports."""

    Modality = InvestigationType.Modality

    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Report available'
        REVIEWED = 'reviewed', 'Reviewed'
        CANCELLED = 'cancelled', 'Cancelled'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='investigations')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='investigations')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    investigation_type = models.ForeignKey(InvestigationType, on_delete=models.PROTECT, null=True, blank=True,
                                           related_name='orders', verbose_name='Investigation')
    modality = models.CharField(max_length=20, choices=Modality.choices, editable=False,
                                help_text='Copied from the investigation type; used by the indicators.')
    description = models.CharField('Procedure / indication', max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    requested_at = models.DateTimeField(default=timezone.now)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    performed_at = models.DateTimeField(null=True, blank=True)
    results = models.JSONField('Structured results', default=dict, blank=True)
    findings = models.TextField('Findings / report', blank=True)
    conclusion = models.CharField(max_length=255, blank=True)
    is_abnormal = models.BooleanField('Abnormal / pathology found', null=True, blank=True,
                                      help_text='e.g. RHD, congenital defect, heart failure on echo.')
    attachment = models.FileField(upload_to='investigations/%Y/%m/', blank=True,
                                  validators=[FileExtensionValidator(['pdf', 'png', 'jpg', 'jpeg'])])
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-requested_at']
        permissions = [('review_investigationorder', 'Can review investigation reports')]
        db_table = 'laboratory_investigationorder'  # moved from the laboratory app; table kept

    def __str__(self):
        return f'{self.name} for {self.patient}'

    @property
    def name(self):
        return self.investigation_type.name if self.investigation_type_id else self.get_modality_display()

    def save(self, *args, **kwargs):
        if self.investigation_type_id:
            self.modality = self.investigation_type.modality
        super().save(*args, **kwargs)

    def result_rows(self):
        """(field, value, abnormal) for the structured results, in catalogue order."""
        if not self.investigation_type_id:
            return []
        return [(f, self.results.get(f.key, ''), f.is_abnormal(self.results.get(f.key, '')))
                for f in self.investigation_type.fields.all() if self.results.get(f.key, '') != '']

    def clean(self):
        if self.status == self.Status.COMPLETED and not (self.results or self.findings or self.attachment):
            raise ValidationError('Enter the results or findings, or attach the report.')

import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from facilities.models import Facility
from patients.models import Patient


class LabCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'lab categories'

    def __str__(self):
        return self.name


class LabTest(models.Model):
    """One laboratory parameter. Units and reference ranges are configuration, editable without code."""

    class ResultType(models.TextChoices):
        NUMERIC = 'numeric', 'Number'
        CHOICE = 'choice', 'Choice (e.g. Positive / Negative)'
        TEXT = 'text', 'Free text'

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    category = models.ForeignKey(LabCategory, on_delete=models.PROTECT, null=True, blank=True, related_name='tests')
    specimen_type = models.CharField(max_length=50, blank=True)
    result_type = models.CharField(max_length=10, choices=ResultType.choices, default=ResultType.NUMERIC)
    unit = models.CharField(max_length=30, blank=True)
    reference_low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    reference_high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    reference_range = models.CharField('Reference range (text)', max_length=100, blank=True,
                                       help_text='Shown to staff, e.g. "Male 13–17, Female 12–15" or "Negative".')
    choices = models.TextField(blank=True, help_text='For choice results: one option per line. '
                                                     'Prefix abnormal options with "!" e.g. "!Positive".')
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category__order', 'order', 'name']

    def __str__(self):
        return f'{self.name} ({self.unit})' if self.unit else self.name

    def clean(self):
        if self.reference_low is not None and self.reference_high is not None and self.reference_low > self.reference_high:
            raise ValidationError({'reference_high': 'Upper limit must be above the lower limit.'})
        if self.result_type == self.ResultType.CHOICE and not self.choice_list():
            raise ValidationError({'choices': 'Add at least one option for a choice result.'})

    def choice_list(self):
        return [line.strip().lstrip('!').strip() for line in self.choices.splitlines() if line.strip()]

    def abnormal_choices(self):
        return {line.strip()[1:].strip() for line in self.choices.splitlines() if line.strip().startswith('!')}

    @property
    def reference_display(self):
        if self.reference_low is not None or self.reference_high is not None:
            low = '' if self.reference_low is None else f'{self.reference_low.normalize():f}'
            high = '' if self.reference_high is None else f'{self.reference_high.normalize():f}'
            text = f'{low}–{high}' if low and high else (f'≥ {low}' if low else f'≤ {high}')
            return f'{text} {self.unit}'.strip()
        return self.reference_range

    def evaluate(self, raw):
        """Return (numeric value or None, abnormal flag) for a raw result."""
        raw = (raw or '').strip()
        if self.result_type == self.ResultType.NUMERIC:
            try:
                value = Decimal(raw.replace(',', '.'))
            except (InvalidOperation, ValueError):
                return None, False
            abnormal = ((self.reference_low is not None and value < self.reference_low)
                        or (self.reference_high is not None and value > self.reference_high))
            return value, abnormal
        if self.result_type == self.ResultType.CHOICE:
            return None, raw in self.abnormal_choices()
        return None, False


class LabPanel(models.Model):
    """A group of tests ordered together, e.g. Full blood count."""

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    tests = models.ManyToManyField(LabTest, related_name='panels')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class LabOrder(TimeStampedModel):
    """Separate request, specimen and result lifecycle for a laboratory test."""

    class Status(models.TextChoices):
        ORDERED = 'ordered', 'Ordered'
        COLLECTED = 'collected', 'Specimen collected'
        RESULTED = 'resulted', 'Resulted'
        REVIEWED = 'reviewed', 'Reviewed'
        CANCELLED = 'cancelled', 'Cancelled'

    class Priority(models.TextChoices):
        ROUTINE = 'routine', 'Routine'
        URGENT = 'urgent', 'Urgent'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='lab_orders')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='lab_orders')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name='orders')
    panel = models.ForeignKey(LabPanel, on_delete=models.PROTECT, null=True, blank=True, related_name='orders')
    request_group = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False,
                                     help_text='Tests requested together share a group (e.g. one panel order).')
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    clinical_notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ORDERED)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    ordered_at = models.DateTimeField(default=timezone.now)
    specimen_id = models.CharField(max_length=50, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='+')
    result_value = models.CharField(max_length=100, blank=True)
    result_numeric = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    result_unit = models.CharField('Unit at time of result', max_length=30, blank=True)
    reference_snapshot = models.CharField('Reference range at time of result', max_length=100, blank=True)
    result_notes = models.TextField(blank=True)
    is_abnormal = models.BooleanField(default=False)
    resulted_at = models.DateTimeField(null=True, blank=True)
    resulted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-ordered_at']
        permissions = [('review_laborder', 'Can review / validate lab and investigation results')]

    def __str__(self):
        return f'{self.test.name} for {self.patient}'

    def record_result(self, raw, user, notes='', when=None, abnormal=None):
        """Store a result with the unit and reference range in force at the time."""
        numeric, flagged = self.test.evaluate(raw)
        self.result_value = (raw or '').strip()
        self.result_numeric = numeric
        self.result_unit = self.test.unit
        self.reference_snapshot = self.test.reference_display
        self.is_abnormal = flagged if abnormal is None else abnormal
        self.result_notes = notes
        self.resulted_at = when or timezone.now()
        self.resulted_by = user
        self.status = self.Status.RESULTED

    @property
    def turnaround_hours(self):
        if self.resulted_at:
            return round((self.resulted_at - self.ordered_at).total_seconds() / 3600, 1)
        return None


class InvestigationOrder(TimeStampedModel):
    """ECG, echocardiography, imaging and other procedure requests and reports."""

    class Modality(models.TextChoices):
        ECG = 'ecg', 'ECG'
        ECHO = 'echo', 'Echocardiography'
        XRAY = 'xray', 'X-ray'
        ULTRASOUND = 'ultrasound', 'Ultrasound'
        CT = 'ct', 'CT scan'
        TCD = 'tcd', 'Transcranial doppler (TCD)'
        OTHER = 'other', 'Other'

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
    modality = models.CharField(max_length=20, choices=Modality.choices)
    description = models.CharField('Procedure / indication', max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    requested_at = models.DateTimeField(default=timezone.now)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    performed_at = models.DateTimeField(null=True, blank=True)
    findings = models.TextField('Findings / report', blank=True)
    conclusion = models.CharField(max_length=255, blank=True)
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

    def __str__(self):
        return f'{self.get_modality_display()} for {self.patient}'

    def clean(self):
        if self.status == self.Status.COMPLETED and not (self.findings or self.attachment):
            raise ValidationError('Enter the findings or attach the report.')

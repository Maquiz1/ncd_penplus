from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import LocationType, TimeStampedModel, WorkflowStage
from facilities.models import Facility
from patients.models import Patient


class FollowUpPlan(TimeStampedModel):
    """Schedule parameters. Visits are generated as Appointments tagged with the plan version."""

    class Anchor(models.TextChoices):
        ENROLLMENT = 'enrollment', 'Enrollment date'
        BASELINE = 'baseline', 'Baseline visit date'
        LAST_VISIT = 'last_visit', 'Last completed visit'
        CUSTOM = 'custom', 'Custom date'

    class Generation(models.TextChoices):
        AUTOMATIC = 'automatic', 'Automatic'
        MANUAL = 'manual', 'Manual'

    INTERVAL_CHOICES = [(1, 'Monthly'), (2, 'Every 2 months'), (3, 'Every 3 months'), (6, 'Every 6 months'),
                        (12, 'Yearly')]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='followup_plans')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    programme_enrollment = models.ForeignKey('clinical.ProgrammeEnrollment', on_delete=models.PROTECT, null=True,
                                             blank=True, related_name='followup_plans')
    study_enrollment = models.ForeignKey('research.StudyEnrollment', on_delete=models.PROTECT, null=True, blank=True,
                                         related_name='followup_plans')
    interval_months = models.PositiveSmallIntegerField(choices=INTERVAL_CHOICES, default=1)
    anchor_type = models.CharField(max_length=20, choices=Anchor.choices, default=Anchor.ENROLLMENT)
    anchor_date = models.DateField(help_text='Date the schedule is calculated from.')
    visit_count = models.PositiveSmallIntegerField('Number of future visits', default=6)
    end_date = models.DateField('Maximum horizon / end date', null=True, blank=True)
    generation = models.CharField(max_length=20, choices=Generation.choices, default=Generation.AUTOMATIC)
    version = models.PositiveSmallIntegerField(default=1, editable=False)
    is_active = models.BooleanField(default=True)
    change_reason = models.CharField(max_length=255, blank=True,
                                     help_text='Required when changing an existing schedule.')

    class Meta:
        ordering = ['-is_active', '-created_at']

    def __str__(self):
        return f'{self.get_interval_months_display()} from {self.anchor_date} (v{self.version})'

    def clean(self):
        if self.programme_enrollment_id and self.study_enrollment_id:
            raise ValidationError('Link the plan to a programme enrollment or a study enrollment, not both.')
        if self.end_date and self.end_date <= self.anchor_date:
            raise ValidationError({'end_date': 'End date must be after the anchor date.'})


class Appointment(TimeStampedModel):
    """A planned contact. Distinct from an Encounter, which is an actual contact."""

    class Status(models.TextChoices):
        EXPECTED = 'expected', 'Expected (historical, to reconcile)'
        SCHEDULED = 'scheduled', 'Scheduled'
        CONFIRMED = 'confirmed', 'Confirmed'
        RESCHEDULED = 'rescheduled', 'Rescheduled'
        CANCELLED = 'cancelled', 'Cancelled'
        ATTENDED = 'attended', 'Attended'
        MISSED = 'missed', 'Missed'

    OPEN_STATUSES = ('expected', 'scheduled', 'confirmed', 'rescheduled')

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='appointments')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='appointments')
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField(null=True, blank=True)
    stage = models.CharField('Visit type', max_length=20, choices=WorkflowStage.choices, default=WorkflowStage.FOLLOW_UP)
    location_type = models.CharField(max_length=20, choices=LocationType.choices, default=LocationType.FACILITY)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    programme_enrollment = models.ForeignKey('clinical.ProgrammeEnrollment', on_delete=models.PROTECT, null=True,
                                             blank=True, related_name='appointments')
    study_enrollment = models.ForeignKey('research.StudyEnrollment', on_delete=models.PROTECT, null=True, blank=True,
                                         related_name='appointments')
    plan = models.ForeignKey(FollowUpPlan, on_delete=models.PROTECT, null=True, blank=True, related_name='appointments',
                             editable=False)
    plan_version = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    sequence = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    reason_for_change = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['scheduled_date', 'scheduled_time']
        constraints = [models.UniqueConstraint(fields=['plan', 'plan_version', 'sequence'],
                                               name='unique_generated_visit')]

    def __str__(self):
        return f'{self.scheduled_date:%d %b %Y} – {self.get_stage_display()} ({self.get_status_display()})'

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_date = instance.__dict__.get('scheduled_date')
        return instance

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class Encounter(TimeStampedModel):
    """An actual contact: facility, home, community or other approved setting."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        IN_PROGRESS = 'in_progress', 'In progress'
        COMPLETED = 'completed', 'Completed'
        INCOMPLETE = 'incomplete', 'Incomplete'
        CANCELLED = 'cancelled', 'Cancelled'

    class HomeVisitOutcome(models.TextChoices):
        COMPLETED = 'completed', 'Completed'
        ABSENT = 'absent', 'Patient absent'
        REFUSED = 'refused', 'Refused'
        RESCHEDULED = 'rescheduled', 'Rescheduled'
        UNSUCCESSFUL = 'unsuccessful', 'Unsuccessful'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='encounters')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='encounters')
    appointment = models.ForeignKey(Appointment, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='encounters', help_text='Leave empty for an unscheduled encounter.')
    encounter_date = models.DateTimeField(default=timezone.now)
    stage = models.CharField('Encounter type / stage', max_length=20, choices=WorkflowStage.choices,
                             default=WorkflowStage.FOLLOW_UP)
    location_type = models.CharField(max_length=20, choices=LocationType.choices, default=LocationType.FACILITY)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    home_visit_outcome = models.CharField(max_length=20, choices=HomeVisitOutcome.choices, blank=True,
                                          help_text='For home/community visit attempts.')
    programme_enrollment = models.ForeignKey('clinical.ProgrammeEnrollment', on_delete=models.PROTECT, null=True,
                                             blank=True, related_name='encounters')
    study_enrollment = models.ForeignKey('research.StudyEnrollment', on_delete=models.PROTECT, null=True, blank=True,
                                         related_name='encounters')
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    chief_complaint = models.CharField(max_length=255, blank=True)
    clinical_notes = models.TextField(blank=True)
    plan_notes = models.TextField('Plan / next steps', blank=True)

    class Meta:
        ordering = ['-encounter_date']

    def __str__(self):
        return f'{self.patient} {self.get_stage_display()} {timezone.localtime(self.encounter_date):%Y-%m-%d}'

    def get_absolute_url(self):
        return reverse('encounters:encounter_detail', args=[self.pk])

    @property
    def is_scheduled(self):
        return self.appointment_id is not None

    def clean(self):
        if self.location_type in ('home', 'community') and self.status == 'completed' and \
                self.home_visit_outcome and self.home_visit_outcome != 'completed':
            raise ValidationError({'status': 'A failed home/community visit attempt cannot be marked completed.'})


class Vitals(TimeStampedModel):
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name='vitals')
    taken_at = models.DateTimeField(default=timezone.now)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    systolic_bp = models.PositiveSmallIntegerField('Systolic BP (mmHg)', null=True, blank=True)
    diastolic_bp = models.PositiveSmallIntegerField('Diastolic BP (mmHg)', null=True, blank=True)
    pulse = models.PositiveSmallIntegerField('Pulse (bpm)', null=True, blank=True)
    temperature_c = models.DecimalField('Temperature (°C)', max_digits=4, decimal_places=1, null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField('SpO₂ (%)', null=True, blank=True)
    blood_glucose = models.DecimalField('Random blood glucose (mmol/L)', max_digits=5, decimal_places=1, null=True,
                                        blank=True)
    waist_cm = models.DecimalField('Waist (cm)', max_digits=5, decimal_places=1, null=True, blank=True)
    muac_cm = models.DecimalField('MUAC (cm)', max_digits=4, decimal_places=1, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-taken_at']
        verbose_name_plural = 'vitals'

    def __str__(self):
        return f'Vitals {self.taken_at:%Y-%m-%d %H:%M}'

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm:
            metres = self.height_cm / Decimal(100)
            return round(self.weight_kg / (metres * metres), 1)
        return None

    def clean(self):
        if self.systolic_bp and self.diastolic_bp and self.diastolic_bp >= self.systolic_bp:
            raise ValidationError({'diastolic_bp': 'Diastolic pressure must be lower than systolic.'})


class Admission(TimeStampedModel):
    class DischargeOutcome(models.TextChoices):
        IMPROVED = 'improved', 'Discharged home - improved'
        REFERRED = 'referred', 'Referred'
        ABSCONDED = 'absconded', 'Absconded'
        AGAINST_ADVICE = 'against_advice', 'Discharged against advice'
        DIED = 'died', 'Died'
        OTHER = 'other', 'Other'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='admissions')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='admissions')
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, null=True, blank=True, related_name='admissions')
    ward = models.CharField(max_length=100)
    admitted_at = models.DateTimeField(default=timezone.now)
    reason = models.CharField('Reason for admission', max_length=255)
    readmission_of = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='readmissions')
    discharged_at = models.DateTimeField(null=True, blank=True)
    discharge_outcome = models.CharField(max_length=20, choices=DischargeOutcome.choices, blank=True)
    discharge_summary = models.TextField(blank=True)

    class Meta:
        ordering = ['-admitted_at']

    def __str__(self):
        return f'{self.patient} {self.ward} {timezone.localtime(self.admitted_at):%Y-%m-%d}'

    @property
    def status(self):
        return 'discharged' if self.discharged_at else 'admitted'

    def clean(self):
        if self.discharged_at and self.discharged_at < self.admitted_at:
            raise ValidationError({'discharged_at': 'Discharge cannot be before admission.'})
        if self.discharged_at and not self.discharge_outcome:
            raise ValidationError({'discharge_outcome': 'Select the discharge outcome.'})

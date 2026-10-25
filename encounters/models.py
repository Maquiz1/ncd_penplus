from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import LocationType, SoftDeleteModel, TimeStampedModel, WorkflowStage
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


SWAHILI_DAYS = ['Jumatatu', 'Jumanne', 'Jumatano', 'Alhamisi', 'Ijumaa', 'Jumamosi', 'Jumapili']
DEFAULT_INVITATION = ('Habari. Unakumbushwa kuhudhuria kliniki ya {facility} siku ya {day} tarehe {date}{time}. '
                      'Kwa msaada piga {contact}.')


class ClinicSession(TimeStampedModel):
    """A clinic day or week when trained staff/specialists see a group of patients together."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Appointments scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    class Allocation(models.TextChoices):
        SAME_DAY = 'same_day', 'Everyone on the first day'
        SPREAD = 'spread', 'Spread patients evenly across the days'

    name = models.CharField(max_length=150, help_text='e.g. PEN-Plus cardiac clinic with JKCI cardiologist')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='clinic_sessions')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text='For a clinic over several days (optional).')
    start_time = models.TimeField(null=True, blank=True)
    programmes = models.ManyToManyField('clinical.ClinicalProgramme', blank=True, related_name='clinic_sessions',
                                        help_text='Leave empty for all programmes.')
    include_ltfu = models.BooleanField('Also invite defaulters / lost to follow-up', default=False,
                                       help_text='Attending counts as returning to care.')
    allocation = models.CharField(max_length=20, choices=Allocation.choices, default=Allocation.SAME_DAY)
    skip_weekends = models.BooleanField(default=True)
    move_existing = models.BooleanField('Move existing open appointments in this period to the clinic day',
                                        default=True)
    team = models.CharField('Specialists / team', max_length=255, blank=True)
    contact_phone = models.CharField('Contact phone for patients', max_length=30, blank=True)
    sms_template = models.TextField('Invitation SMS', default=DEFAULT_INVITATION,
                                    help_text='Placeholders: {facility} {day} {date} {time} {contact}. '
                                              'Do not include diagnoses or study names.')
    send_without_consent = models.BooleanField(
        'Also send to patients without recorded SMS consent', default=False,
        help_text='Patients whose consent was not asked yet. Patients who said No never receive SMS.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ['-start_date', '-pk']

    def __str__(self):
        return f'{self.name} ({self.start_date:%d %b %Y})'

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('encounters:clinic_detail', args=[self.pk])

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'The last day cannot be before the first day.'})

    def days(self):
        import datetime
        days, day = [], self.start_date
        while day <= (self.end_date or self.start_date):
            if not (self.skip_weekends and day.weekday() >= 5) or day == self.start_date:
                days.append(day)
            day += datetime.timedelta(days=1)
        return days

    def render_sms(self, date):
        time = f' saa {self.start_time:%H:%M}' if self.start_time else ''
        return self.sms_template.format(facility=self.facility.name, day=SWAHILI_DAYS[date.weekday()],
                                        date=f'{date:%d/%m/%Y}', time=time,
                                        contact=self.contact_phone or self.facility.name)


class Appointment(SoftDeleteModel):
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

    class Origin(models.TextChoices):
        MANUAL = 'manual', 'Booked by staff'
        SCHEDULE = 'schedule', 'Generated from follow-up schedule'
        EDCS = 'edcs', 'Migrated from EDCS'
        EDCS_GAP = 'edcs_gap', 'Month without any EDCS visit (marked missed)'
        CLINIC_DAY = 'clinic_day', 'Clinic day (scheduled for a group)'

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
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.MANUAL, editable=False)
    clinic_session = models.ForeignKey('ClinicSession', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='appointments', editable=False)

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


class Encounter(SoftDeleteModel):
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
        if self.stage == WorkflowStage.UNSCHEDULED and self.appointment_id:
            raise ValidationError({'appointment': 'An unscheduled visit is not linked to an appointment. Leave it '
                                                  'empty, or choose Follow-up as the visit type.'})
        if self.location_type in ('home', 'community') and self.status == 'completed' and \
                self.home_visit_outcome and self.home_visit_outcome != 'completed':
            raise ValidationError({'status': 'A failed home/community visit attempt cannot be marked completed.'})


class Vitals(SoftDeleteModel):
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


class Admission(SoftDeleteModel):
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

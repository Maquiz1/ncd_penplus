from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import LocationType, SoftDeleteModel, TimeStampedModel
from facilities.models import Facility
from patients.models import Patient


class ActiveDiseaseManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Disease(models.Model):
    """A disease area (e.g. Diabetes). Inactive or deleted diseases are hidden from forms, indicators and reports;
    their history is kept. Managed in Diseases (the diseases app)."""

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+', editable=False)
    deletion_reason = models.CharField(max_length=255, blank=True, editable=False)

    objects = ActiveDiseaseManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['order', 'name']
        permissions = [('restore_disease', 'Can restore deleted diseases')]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('diseases:detail', args=[self.pk])


class ClinicalProgramme(models.Model):
    disease = models.ForeignKey(Disease, on_delete=models.PROTECT, related_name='programmes')
    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PreScreening(SoftDeleteModel):
    """General pre-screening, before any disease-specific screening."""

    class Disposition(models.TextChoices):
        PROCEED = 'proceed', 'Proceed to disease screening'
        NOT_NOW = 'not_now', 'Does not proceed at this time'
        REFERRED = 'referred', 'Referred elsewhere'

    class Type(models.TextChoices):
        FACILITY = 'facility', 'Facility'
        COMMUNITY = 'community', 'Community outreach'
        SCHOOL = 'school', 'School outreach'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='prescreenings')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    date = models.DateField('Date of screening', default=timezone.localdate)
    screening_type = models.CharField('Type of screening', max_length=20, choices=Type.choices, default=Type.FACILITY)
    consent_obtained = models.BooleanField('Consenting individual?', null=True, blank=True)
    consent_date = models.DateField('Date of consent', null=True, blank=True)
    resides_in_area = models.BooleanField('Permanent resident (lives in the catchment / study area)', default=True)
    expected_stay_months = models.PositiveSmallIntegerField('Expected stay in area (months)', null=True, blank=True)
    known_ncd = models.BooleanField('Known NCD?', null=True, blank=True,
                                    help_text='Already diagnosed with an NCD before today (e.g. a known diabetic).')
    lab_test_requested = models.BooleanField('Request lab test?', null=True, blank=True)
    lab_request_date = models.DateField('Lab request date', null=True, blank=True)
    disposition = models.CharField(max_length=20, choices=Disposition.choices, default=Disposition.PROCEED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-pk']
        verbose_name = 'pre-screening'

    def __str__(self):
        return f'Pre-screening {self.patient} {self.date}'


class Screening(SoftDeleteModel):
    """One disease-specific screening event. Never creates a diagnosis or enrollment by itself."""

    class Source(models.TextChoices):
        FACILITY = 'facility', 'Facility'
        COMMUNITY = 'community', 'Community outreach'
        SCHOOL = 'school', 'School outreach'

    class Result(models.TextChoices):
        PENDING = 'pending', 'Pending'
        POSITIVE = 'positive', 'Positive'
        NEGATIVE = 'negative', 'Negative'
        INCONCLUSIVE = 'inconclusive', 'Inconclusive'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='screenings')
    disease = models.ForeignKey(Disease, on_delete=models.PROTECT, related_name='screenings')
    session = models.ForeignKey('ScreeningSession', on_delete=models.PROTECT, null=True, blank=True,
                                related_name='screenings')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='screenings')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    source = models.CharField('Screening source', max_length=20, choices=Source.choices, default=Source.FACILITY)
    location_type = models.CharField('Actual location', max_length=20, choices=LocationType.choices,
                                     default=LocationType.FACILITY)
    screening_date = models.DateField(default=timezone.localdate)
    result = models.CharField(max_length=20, choices=Result.choices, default=Result.PENDING)
    risk_factors = models.TextField(blank=True)
    next_action = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    consent_obtained = models.BooleanField('Consent obtained', default=False)
    assent_obtained = models.BooleanField('Assent obtained (participant under 18)', default=False)
    guardian_name = models.CharField('Parent / guardian who consented', max_length=150, blank=True)
    referred = models.BooleanField('Informed and referred', default=False)
    referred_to = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                    help_text='Facility the person was referred to for further management.')
    screened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-screening_date', '-pk']

    def __str__(self):
        return f'{self.disease} screening {self.screening_date}'


class ScreeningSession(TimeStampedModel):
    """A screening campaign day: a school, village/street, outreach point or facility clinic."""

    name = models.CharField(max_length=150, help_text='e.g. Kondoa Primary School outreach')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='screening_sessions',
                                 help_text='Responsible facility. New people are registered here.')
    session_date = models.DateField(default=timezone.localdate)
    source = models.CharField('Screening source', max_length=20, choices=Screening.Source.choices,
                              default=Screening.Source.COMMUNITY)
    location_type = models.CharField('Actual location', max_length=20, choices=LocationType.choices,
                                     default=LocationType.COMMUNITY)
    place_name = models.CharField('School / village / street', max_length=150)
    district = models.ForeignKey('facilities.District', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='+')
    ward = models.ForeignKey('facilities.Ward', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    street = models.ForeignKey('facilities.Street', on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                               verbose_name='Street / village')
    diseases = models.ManyToManyField(Disease, related_name='screening_sessions')
    min_age = models.PositiveSmallIntegerField('Minimum age (years)', default=6)
    team_notes = models.TextField(blank=True)
    is_closed = models.BooleanField('Closed (no more entries)', default=False)

    class Meta:
        ordering = ['-session_date', '-pk']

    def __str__(self):
        return f'{self.name} ({self.session_date:%d %b %Y})'

    def get_absolute_url(self):
        return reverse('clinical:session_detail', args=[self.pk])


class Diagnosis(SoftDeleteModel):
    class Status(models.TextChoices):
        PROVISIONAL = 'provisional', 'Provisional / suspected'
        CONFIRMED = 'confirmed', 'Confirmed'
        RULED_OUT = 'ruled_out', 'Ruled out'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='diagnoses')
    disease = models.ForeignKey(Disease, on_delete=models.PROTECT, related_name='diagnoses')
    encounter = models.ForeignKey('encounters.Encounter', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='diagnoses')
    description = models.CharField('Condition / specific diagnosis', max_length=255,
                                   help_text='e.g. Type 2 diabetes mellitus, Rheumatic heart disease, HbSS')
    code = models.CharField('Code (ICD-10/11)', max_length=20, blank=True)
    condition = models.ForeignKey('diseases.Condition', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='diagnoses', help_text='Reporting condition; set automatically, '
                                                                      'change it if it is wrong.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROVISIONAL)
    onset_date = models.DateField(null=True, blank=True)
    diagnosis_date = models.DateField(default=timezone.localdate)
    complications = models.TextField('Comorbidities / complications', blank=True)
    clinician = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-diagnosis_date', '-pk']
        verbose_name_plural = 'diagnoses'

    def __str__(self):
        return f'{self.description} ({self.get_status_display()})'

    def save(self, *args, **kwargs):
        if self.condition_id is None and self.disease_id:
            from diseases.models import match_condition
            self.condition, _matched = match_condition(self.description, self.disease)
        super().save(*args, **kwargs)


class EnrollmentStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    ACTIVE = 'active', 'Active'
    COMPLETED = 'completed', 'Completed'
    LTFU = 'ltfu', 'Lost to follow-up'
    TRANSFERRED_OUT = 'transferred_out', 'Transferred out'
    WITHDRAWN = 'withdrawn', 'Withdrawn'
    DECEASED = 'deceased', 'Deceased'
    OTHER = 'other', 'Other closure'


CLOSED_RETURNABLE = ('ltfu', 'transferred_out', 'withdrawn', 'other', 'completed')


class ProgrammeEnrollment(TimeStampedModel):
    """Clinical programme participation, independent of registration and research."""

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='programme_enrollments')
    programme = models.ForeignKey(ClinicalProgramme, on_delete=models.PROTECT, related_name='enrollments')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+',
                                 help_text='Enrolling facility')
    enrollment_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=EnrollmentStatus.choices, default=EnrollmentStatus.ACTIVE)
    responsible_team = models.CharField(max_length=150, blank=True)
    care_plan = models.TextField(blank=True)
    outcome_date = models.DateField(null=True, blank=True)
    outcome_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-enrollment_date', '-pk']
        constraints = [models.UniqueConstraint(
            fields=['patient', 'programme'], condition=models.Q(status__in=['pending', 'active']),
            name='one_open_enrollment_per_programme')]

    def __str__(self):
        return f'{self.programme} ({self.get_status_display()})'

    def clean(self):
        closed = self.status not in ('pending', 'active')
        if closed and not self.outcome_date:
            raise ValidationError({'outcome_date': 'An outcome date is required when closing the enrollment.'})
        if self.outcome_date and self.outcome_date < self.enrollment_date:
            raise ValidationError({'outcome_date': 'Outcome date cannot be before enrollment date.'})
        if self.patient_id and self.programme_id and not closed:
            clash = ProgrammeEnrollment.objects.filter(patient_id=self.patient_id, programme_id=self.programme_id,
                                                       status__in=['pending', 'active']).exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError('The subject already has an open enrollment in this programme.')


class EnrollmentEvent(TimeStampedModel):
    """History of a programme enrollment's status: closures, returns to care, transfers in."""

    class Kind(models.TextChoices):
        STATUS = 'status', 'Status change'
        RETURN = 'return', 'Returned to care'
        TRANSFER_IN = 'transfer_in', 'Transferred in'

    enrollment = models.ForeignKey(ProgrammeEnrollment, on_delete=models.PROTECT, related_name='events')
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.STATUS)
    event_date = models.DateField()
    from_status = models.CharField(max_length=20, choices=EnrollmentStatus.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=EnrollmentStatus.choices)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-event_date', '-pk']

    def __str__(self):
        return f'{self.enrollment}: {self.from_status or "—"} → {self.to_status} on {self.event_date}'


class FamilyMember(SoftDeleteModel):
    """A relative of the patient, mainly siblings for sickle cell family screening (no names are recorded)."""

    class Relation(models.TextChoices):
        SIBLING = 'sibling', 'Sibling'
        PARENT = 'parent', 'Parent'
        CHILD = 'child', 'Child'
        OTHER = 'other', 'Other relative'

    class ScdStatus(models.TextChoices):
        AFFECTED = 'affected', 'Has sickle cell disease'
        CARRIER = 'carrier', 'Carrier (trait)'
        NOT_AFFECTED = 'not_affected', 'Not affected'
        UNKNOWN = 'unknown', 'Unknown / not tested'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='family_members')
    relation = models.CharField(max_length=10, choices=Relation.choices, default=Relation.SIBLING)
    sex = models.CharField(max_length=10, choices=[('male', 'Male'), ('female', 'Female')], blank=True)
    age_years = models.PositiveSmallIntegerField('Age (years)', null=True, blank=True)
    alive = models.BooleanField(null=True, blank=True)
    scd_status = models.CharField('Sickle cell status', max_length=15, choices=ScdStatus.choices,
                                  default=ScdStatus.UNKNOWN)
    tested = models.BooleanField('Tested for sickle cell', null=True, blank=True)
    test_date = models.DateField(null=True, blank=True)
    test_type = models.CharField(max_length=60, blank=True, help_text='e.g. Sickle SCAN, HPLC, sickling test')
    referred_for_screening = models.BooleanField(default=False)
    recorded_on = models.DateField(default=timezone.localdate)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['relation', '-age_years', 'pk']

    def __str__(self):
        return f'{self.get_relation_display()} of {self.patient} ({self.get_scd_status_display()})'

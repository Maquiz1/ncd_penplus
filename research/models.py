from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from clinical.models import Disease
from core.models import TimeStampedModel
from facilities.models import Facility
from patients.models import Patient


class Study(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        ACTIVE = 'active', 'Active (recruiting)'
        FOLLOW_UP = 'follow_up', 'Follow-up only'
        CLOSED = 'closed', 'Closed'

    code = models.SlugField(max_length=30, unique=True)
    title = models.CharField(max_length=255)
    protocol_number = models.CharField(max_length=100, blank=True)
    principal_investigator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                               blank=True, related_name='+')
    diseases = models.ManyToManyField(Disease, blank=True, related_name='studies')
    sites = models.ManyToManyField(Facility, blank=True, related_name='studies')
    current_consent_version = models.CharField(max_length=50, blank=True)
    participant_id_prefix = models.CharField(max_length=20, blank=True,
                                             help_text='Used to suggest study participant IDs, e.g. DM-STUDY-')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    eligibility_criteria = models.TextField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['code']
        verbose_name_plural = 'studies'

    def __str__(self):
        return f'{self.code} — {self.title}'

    def get_absolute_url(self):
        return reverse('research:study_detail', args=[self.pk])


class StudyEligibilityAssessment(TimeStampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='study_eligibility')
    study = models.ForeignKey(Study, on_delete=models.PROTECT, related_name='eligibility_assessments')
    assessed_on = models.DateField(default=timezone.localdate)
    is_eligible = models.BooleanField('Eligible')
    criteria_notes = models.TextField('Criteria assessed', blank=True)
    reason = models.CharField('Reason (if ineligible)', max_length=255, blank=True)
    assessed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-assessed_on', '-pk']

    def __str__(self):
        return f'{self.study.code}: {"eligible" if self.is_eligible else "ineligible"} on {self.assessed_on}'

    def clean(self):
        if self.is_eligible is False and not self.reason:
            raise ValidationError({'reason': 'Give the reason the subject is ineligible.'})


class ConsentRecord(TimeStampedModel):
    class Decision(models.TextChoices):
        CONSENTED = 'consented', 'Consented'
        DECLINED = 'declined', 'Declined'
        WITHDRAWN = 'withdrawn', 'Withdrawn'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='consents')
    study = models.ForeignKey(Study, on_delete=models.PROTECT, related_name='consents')
    consent_type = models.CharField(max_length=100, default='Main study consent')
    consent_version = models.CharField(max_length=50)
    decision = models.CharField(max_length=20, choices=Decision.choices)
    consent_date = models.DateTimeField(default=timezone.now)
    obtained_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    witness_name = models.CharField(max_length=150, blank=True)
    legally_authorized_representative = models.CharField(max_length=150, blank=True)
    withdrawal_date = models.DateField(null=True, blank=True)
    withdrawal_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-consent_date', '-pk']

    def __str__(self):
        return f'{self.study.code} consent v{self.consent_version}: {self.get_decision_display()}'

    def clean(self):
        if self.decision == self.Decision.WITHDRAWN and not self.withdrawal_date:
            raise ValidationError({'withdrawal_date': 'Record the date consent was withdrawn.'})


class StudyEnrollment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
        LTFU = 'ltfu', 'Lost to follow-up'
        TRANSFERRED = 'transferred', 'Transferred'
        DECEASED = 'deceased', 'Deceased'
        OTHER = 'other', 'Protocol-defined closure'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='study_enrollments')
    study = models.ForeignKey(Study, on_delete=models.PROTECT, related_name='enrollments')
    participant_id = models.CharField('Study participant ID', max_length=50)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='+')
    eligibility = models.ForeignKey(StudyEligibilityAssessment, on_delete=models.PROTECT, related_name='+')
    consent = models.ForeignKey(ConsentRecord, on_delete=models.PROTECT, related_name='+')
    enrollment_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    outcome_date = models.DateField(null=True, blank=True)
    outcome_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-enrollment_date', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['study', 'participant_id'], name='unique_participant_id_per_study'),
            models.UniqueConstraint(fields=['patient', 'study'], condition=models.Q(status__in=['pending', 'active']),
                                    name='one_open_enrollment_per_study'),
        ]

    def __str__(self):
        return f'{self.study.code} / {self.participant_id}'

    def clean(self):
        errors = {}
        if self.eligibility_id:
            if self.eligibility.patient_id != self.patient_id or self.eligibility.study_id != self.study_id:
                errors['eligibility'] = 'Eligibility assessment must be for this subject and study.'
            elif not self.eligibility.is_eligible:
                errors['eligibility'] = 'The subject was assessed as ineligible for this study.'
        if self.consent_id:
            if self.consent.patient_id != self.patient_id or self.consent.study_id != self.study_id:
                errors['consent'] = 'Consent must be for this subject and study.'
            elif self.consent.decision != ConsentRecord.Decision.CONSENTED and self.status in ('pending', 'active'):
                errors['consent'] = 'Enrollment requires a consent record with decision "Consented".'
        if self.status not in ('pending', 'active') and not self.outcome_date:
            errors['outcome_date'] = 'An outcome date is required when closing the enrollment.'
        if self.patient_id and self.study_id and self.status in ('pending', 'active'):
            # Checked here as well as by the partial unique constraint, which MySQL cannot create.
            open_elsewhere = StudyEnrollment.objects.filter(patient_id=self.patient_id, study_id=self.study_id,
                                                            status__in=['pending', 'active']).exclude(pk=self.pk)
            if open_elsewhere.exists():
                errors['study'] = 'The subject already has an open enrollment in this study.'
        if errors:
            raise ValidationError(errors)

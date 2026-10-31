from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import NotDeletedManager, SoftDeleteModel, TimeStampedModel
from facilities.models import District, Facility, Street, Ward
from facilities.scope import scope_queryset


IDENTITY_FIELDS = {
    'patients.Patient': {'first_name', 'middle_name', 'last_name', 'date_of_birth', 'phone', 'alternate_phone',
                         'address', 'residence_ward', 'residence_street', 'next_of_kin_name', 'next_of_kin_phone', 'cause_of_death'},
    'patients.PatientIdentifier': {'value'},
    'clinical.Screening': {'guardian_name'},
    'research.ConsentRecord': {'witness_name', 'legally_authorized_representative'},
}


class PatientQuerySet(models.QuerySet):
    def in_scope(self, user):
        return scope_queryset(self, user, 'current_facility')


class PatientManager(NotDeletedManager.from_queryset(PatientQuerySet)):
    pass


class Patient(SoftDeleteModel):
    """Master patient identity shared by clinical care and research."""

    class Sex(models.TextChoices):
        FEMALE = 'female', 'Female'
        MALE = 'male', 'Male'
        OTHER = 'other', 'Other / unknown'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    class VitalStatus(models.TextChoices):
        ALIVE = 'alive', 'Alive'
        DECEASED = 'deceased', 'Deceased'

    patient_number = models.CharField('Subject ID', max_length=20, unique=True, editable=False)
    old_id = models.PositiveIntegerField('Old ID', unique=True, null=True, blank=True, editable=False,
                                         help_text='Client ID in the previous PEN-Plus EDCS (clients.id).')
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    sex = models.CharField(max_length=10, choices=Sex.choices)
    date_of_birth = models.DateField()
    dob_estimated = models.BooleanField('Date of birth is estimated', default=False)
    phone = models.CharField(max_length=30, blank=True)
    phone_verified = models.BooleanField(default=False, editable=False)
    alternate_phone = models.CharField(max_length=30, blank=True)
    sms_consent = models.BooleanField('Consents to SMS reminders', null=True, blank=True, default=None,
                                      help_text='Ask the subject (or guardian). Messages are only sent after a Yes.')
    sms_consent_recorded_at = models.DateTimeField(null=True, blank=True, editable=False)
    residence_district = models.ForeignKey(District, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    residence_ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    residence_street = models.ForeignKey(Street, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                         verbose_name='Street / village')
    address = models.CharField('House / landmark', max_length=255, blank=True,
                               help_text='e.g. near the church, house number, directions.')
    next_of_kin_name = models.CharField(max_length=200, blank=True)
    next_of_kin_phone = models.CharField(max_length=30, blank=True)
    registration_facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='registered_patients')
    current_facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='current_patients',
                                         editable=False)
    registration_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    vital_status = models.CharField(max_length=10, choices=VitalStatus.choices, default=VitalStatus.ALIVE, editable=False)
    date_of_death = models.DateField(null=True, blank=True, editable=False)
    cause_of_death = models.CharField(max_length=255, blank=True, editable=False)

    objects = PatientManager()
    all_objects = PatientQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        permissions = [
            ('view_patient_identity', 'Can view subject identity fields (names, phones, identifiers)'),
            ('view_subject_summary', 'Can view de-identified subject summaries'),
            ('restore_patient', 'Can restore soft-deleted subjects'),
            ('record_death', 'Can record subject death'),
            ('record_sms_consent', 'Can record SMS consent at a visit'),
        ]
        indexes = [models.Index(fields=['last_name', 'first_name']), models.Index(fields=['phone'])]

    def __str__(self):
        return self.patient_number

    def get_absolute_url(self):
        return reverse('patients:detail', args=[self.pk])

    @property
    def full_name(self):
        return ' '.join(part for part in (self.first_name, self.middle_name, self.last_name) if part)

    @property
    def age(self):
        today = timezone.localdate()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def is_deceased(self):
        return self.vital_status == self.VitalStatus.DECEASED

    @property
    def old_study_id(self):
        """Full old EDCS study ID (e.g. 1-001). Lists load it with with_old_study_id() to avoid a query per row."""
        if not self.old_id:
            return ''
        if not hasattr(self, 'edcs_identifiers'):
            self.edcs_identifiers = list(self.identifiers.filter(id_type=PatientIdentifier.IdType.EDCS_STUDY))
        return self.edcs_identifiers[0].value if self.edcs_identifiers else ''


def with_old_study_id(queryset, path=''):
    """Prefetch the old EDCS study IDs of the patients (or of the rows' patients through `path`, e.g. 'patient')."""
    return queryset.prefetch_related(models.Prefetch(
        f'{path}__identifiers' if path else 'identifiers', to_attr='edcs_identifiers',
        queryset=PatientIdentifier.objects.filter(id_type=PatientIdentifier.IdType.EDCS_STUDY)))


class PatientIdSequence(models.Model):
    """Per-year counter for NPYY-NNNN identifiers; issued numbers are never reused."""

    year = models.PositiveSmallIntegerField(unique=True)
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'{self.year}: {self.last_value}'


class PatientIdentifier(TimeStampedModel):
    class IdType(models.TextChoices):
        HOSPITAL = 'hospital', 'Hospital / MRN number'
        NATIONAL = 'national', 'National ID (NIDA)'
        INSURANCE = 'insurance', 'Insurance number'
        CTC = 'ctc', 'CTC / clinic number'
        GOTHOMIS = 'gothomis', 'GoTHOMIS ID'
        EDCS_STUDY = 'edcs_study', 'Old EDCS study ID'
        OTHER = 'other', 'Other'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='identifiers')
    id_type = models.CharField('Type', max_length=20, choices=IdType.choices)
    value = models.CharField(max_length=100)
    issuer = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True,
                               help_text='Issuing facility, where relevant.')
    is_verified = models.BooleanField(default=False)

    class Meta:
        ordering = ['id_type']
        constraints = [models.UniqueConstraint(fields=['id_type', 'value', 'issuer'], name='unique_identifier_per_issuer')]

    def __str__(self):
        return f'{self.get_id_type_display()} for {self.patient}'


class PatientFacilityAssignment(TimeStampedModel):
    """History of the facility responsible for the patient's care."""

    class Reason(models.TextChoices):
        REGISTRATION = 'registration', 'Registration'
        TRANSFER = 'transfer', 'Transfer'
        CORRECTION = 'correction', 'Correction'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='facility_assignments')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='patient_assignments')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ['-start_date', '-pk']
        constraints = [models.UniqueConstraint(fields=['patient'], condition=models.Q(is_current=True),
                                               name='one_current_assignment_per_patient')]

    def __str__(self):
        return f'{self.patient} @ {self.facility} from {self.start_date}'


class PatientTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        APPROVED = 'approved', 'Approved'
        SENT = 'sent', 'Sent'
        RECEIVED = 'received', 'Received'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'

    # Allowed transitions and which side of the transfer may perform them.
    TRANSITIONS = {
        'approve': ('requested', 'approved', 'from'),
        'send': ('approved', 'sent', 'from'),
        'receive': ('sent', 'received', 'to'),
        'reject': ('sent', 'rejected', 'to'),
        'cancel': (('requested', 'approved'), 'cancelled', 'from'),
    }

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='transfers')
    from_facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='transfers_out', editable=False)
    to_facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='transfers_in')
    reason = models.CharField(max_length=255)
    study_area_note = models.CharField(
        'Study-area classification', max_length=255, blank=True,
        help_text='Whether the destination is inside the study area. A clinical transfer does not end research enrollment.')
    request_date = models.DateField(default=timezone.localdate)
    effective_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, editable=False)
    handover_notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='+', editable=False)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+', editable=False)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-request_date', '-pk']

    def __str__(self):
        return f'{self.patient}: {self.from_facility} → {self.to_facility}'

    @property
    def is_open(self):
        return self.status in ('requested', 'approved', 'sent')


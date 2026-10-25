from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Creation/update timestamps and the staff member who created the record."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='+', editable=False,
    )

    class Meta:
        abstract = True


class NotDeletedManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(TimeStampedModel):
    """Recoverable deletion: rows are hidden from routine lists but never erased."""

    is_deleted = models.BooleanField(default=False, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='+', editable=False,
    )
    deletion_reason = models.CharField(max_length=255, blank=True, editable=False)

    objects = NotDeletedManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def soft_delete(self, user, reason=''):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.deletion_reason = reason
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason', 'updated_at'])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.deletion_reason = ''
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason', 'updated_at'])


class LocationType(models.TextChoices):
    FACILITY = 'facility', 'Facility'
    HOME = 'home', 'Home'
    COMMUNITY = 'community', 'Community'
    SCHOOL = 'school', 'School'
    EMERGENCY = 'emergency', 'Emergency'
    OTHER = 'other', 'Other'


class WorkflowStage(models.TextChoices):
    REGISTRATION = 'registration', 'Registration'
    PRE_SCREENING = 'pre_screening', 'Pre-screening'
    SCREENING = 'screening', 'Disease screening'
    ASSESSMENT = 'assessment', 'Clinical assessment'
    DIAGNOSIS = 'diagnosis', 'Diagnosis'
    BASELINE = 'baseline', 'Baseline'
    FOLLOW_UP = 'follow_up', 'Follow-up'
    UNSCHEDULED = 'unscheduled', 'Unscheduled'
    ADMISSION = 'admission', 'Admission'
    DISCHARGE = 'discharge', 'Discharge'
    OUTCOME = 'outcome', 'Outcome'

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse

from core.models import TimeStampedModel
from facilities.models import Facility
from facilities.scope import facilities_for_user
from patients.models import Patient


class DataIssueQuerySet(models.QuerySet):
    def in_scope(self, user):
        if user.is_superuser:
            return self
        facilities = facilities_for_user(user)
        return self.filter(models.Q(patient__current_facility__in=facilities)
                           | models.Q(patient__isnull=True, facility__in=facilities)
                           | models.Q(patient__isnull=True, facility__isnull=True))

    def open(self):
        return self.filter(status=DataIssue.Status.OPEN)


class DataIssue(TimeStampedModel):
    """A record that needs a human decision: found during migration or by the data-quality checks."""

    class Category(models.TextChoices):
        VISITS = 'visits', 'Visits & schedule'
        IDENTITY = 'identity', 'Identity & duplicates'
        CONSENT = 'consent', 'Consent & enrollment'
        CLINICAL = 'clinical', 'Clinical data'
        VITALS = 'vitals', 'Vital signs'
        LAB = 'lab', 'Laboratory'
        PHARMACY = 'pharmacy', 'Pharmacy'
        IMAGING = 'imaging', 'ECG / echo / imaging'
        MIGRATION = 'migration', 'Unlinked migrated records'
        LOCATIONS = 'locations', 'Locations & addresses'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'
        DISMISSED = 'dismissed', 'Dismissed (no change needed)'

    key = models.CharField(max_length=150, unique=True, help_text='Stable key so checks never create duplicates.')
    code = models.CharField(max_length=60, db_index=True)
    category = models.CharField(max_length=20, choices=Category.choices, db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, null=True, blank=True, related_name='data_issues')
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    related = GenericForeignKey('content_type', 'object_id')
    related_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    objects = DataIssueQuerySet.as_manager()

    class Meta:
        ordering = ['status', 'category', '-related_date', '-pk']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('issues:detail', args=[self.pk])

    @property
    def related_url(self):
        obj = self.related
        if obj is not None and hasattr(obj, 'get_absolute_url'):
            return obj.get_absolute_url()
        return self.patient.get_absolute_url() if self.patient_id else ''

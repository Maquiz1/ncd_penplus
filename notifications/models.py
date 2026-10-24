from django.db import models
from django.conf import settings

from core.models import TimeStampedModel
from patients.models import Patient


class SmsMessage(TimeStampedModel):
    """One SMS to one patient: queued, then sent through the configured gateway, with its delivery state."""

    class Purpose(models.TextChoices):
        CLINIC_INVITE = 'clinic_invite', 'Clinic day invitation'
        REMINDER_DAY_BEFORE = 'reminder_before', 'Reminder – day before'
        REMINDER_SAME_DAY = 'reminder_today', 'Reminder – same day'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        SENT = 'sent', 'Sent'
        DELIVERED = 'delivered', 'Delivered'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='sms_messages')
    appointment = models.ForeignKey('encounters.Appointment', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='sms_messages')
    clinic_session = models.ForeignKey('encounters.ClinicSession', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='sms_messages')
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    phone = models.CharField(max_length=20, help_text='International format, e.g. 255712345678')
    recipient = models.CharField(max_length=20, default='patient', help_text='patient or next of kin / guardian')
    body = models.TextField()
    for_date = models.DateField(null=True, blank=True, help_text='Appointment date the message refers to.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    backend = models.CharField(max_length=30, blank=True)
    provider_reference = models.CharField(max_length=100, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['appointment', 'purpose', 'for_date'],
                                               name='one_message_per_appointment_purpose_date')]

    def __str__(self):
        return f'{self.get_purpose_display()} to {self.patient} ({self.get_status_display()})'


class SiteConfiguration(TimeStampedModel):
    """Public address and sender used in system emails; the active one overrides SITE_URL / DEFAULT_FROM_EMAIL."""

    name = models.CharField(max_length=100, unique=True, help_text='e.g. Production, Training server.')
    site_url = models.URLField(help_text='Address staff open in the browser, e.g. https://ncd.nimr.or.tz')
    from_name = models.CharField(max_length=100, blank=True, help_text='Sender name, e.g. Afya NCD.')
    from_email = models.EmailField(
        blank=True, help_text='Must be a verified sender in the mail service (e.g. SendGrid). '
                              'Leave blank to use DEFAULT_FROM_EMAIL from .env.')
    is_active = models.BooleanField(default=False, help_text='Only one configuration is active at a time.')

    class Meta:
        ordering = ['-is_active', 'name']

    def __str__(self):
        return f'{self.name} ({self.site_url})'

    def save(self, *args, **kwargs):
        self.site_url = self.site_url.rstrip('/')
        super().save(*args, **kwargs)
        if self.is_active:
            SiteConfiguration.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)


class EmailLog(models.Model):
    """One email to one recipient and whether the mail server accepted it."""

    class Purpose(models.TextChoices):
        ACTIVATION = 'activation', 'Account activation'
        PASSWORD_RESET = 'password_reset', 'Password reset'
        MONTHLY_REPORT = 'monthly_report', 'Monthly report'
        TEST = 'test', 'Test email'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    recipient = models.EmailField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='email_logs', help_text='Staff account the email was for.')
    subject = models.CharField(max_length=255)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.OTHER, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    error = models.TextField(blank=True)
    backend = models.CharField(max_length=100, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', help_text='Staff member who triggered it (blank for scheduled jobs).')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.subject} → {self.recipient} ({self.status})'

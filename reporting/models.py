from django.conf import settings
from django.db import models


class ReportRun(models.Model):
    """Log of each scheduled report generated for a recipient and their access scope."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        GENERATING = 'generating', 'Generating'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    report_type = models.CharField(max_length=50, default='monthly_enrollment')
    period_start = models.DateField()
    period_end = models.DateField()
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='report_runs')
    scope_description = models.CharField(max_length=500)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    generated_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    reissue_of = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='reissues')
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-period_start', 'recipient']
        permissions = [('view_reports', 'Can view dashboards and reports'),
                       ('export_data', 'Can export subject-level data'),
                       ('receive_monthly_report', 'Receives the monthly enrollment report')]

    def __str__(self):
        return f'{self.report_type} {self.period_start:%Y-%m} → {self.recipient}'

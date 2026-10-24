from django.conf import settings
from django.db import models
from django.utils import timezone


class ExportJob(models.Model):
    """One data export: built in the background (Celery), downloadable only by the person who asked for it."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Building'
        DONE = 'done', 'Ready'
        FAILED = 'failed', 'Failed'
        EXPIRED = 'expired', 'Expired (file removed)'

    class Format(models.TextChoices):
        CSV = 'csv', 'CSV'
        XLSX = 'xlsx', 'Excel'

    class Mode(models.TextChoices):
        LABELS = 'labels', 'Labels (readable answers)'
        CODES = 'codes', 'Codes (values for analysis)'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='export_jobs')
    dataset = models.CharField(max_length=60)
    dataset_label = models.CharField(max_length=200)
    fmt = models.CharField('Format', max_length=5, choices=Format.choices, default=Format.XLSX)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.LABELS)
    deidentified = models.BooleanField(default=True)
    filters = models.JSONField(default=dict, blank=True)
    filters_label = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    total = models.PositiveIntegerField(default=0)
    done = models.PositiveIntegerField(default=0)
    rows = models.PositiveIntegerField(default=0)
    file_name = models.CharField(max_length=200, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    downloads = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        permissions = [('export_identifiable', 'Can export patient names and identifiers')]

    def __str__(self):
        return f'{self.dataset_label} ({self.get_fmt_display()}) by {self.user}'

    @property
    def progress(self):
        if self.status == self.Status.DONE:
            return 100
        return int(100 * self.done / self.total) if self.total else 0

    @property
    def expires_at(self):
        if self.finished_at is None:
            return None
        return self.finished_at + timezone.timedelta(days=settings.EXPORT_RETENTION_DAYS)

    @property
    def path(self):
        return settings.EXPORT_ROOT / self.file_name if self.file_name else None

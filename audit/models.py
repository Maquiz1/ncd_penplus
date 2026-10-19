from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Protected history of material changes and sensitive actions.

    Rows are append-only: the admin is read-only and save() refuses updates.
    """

    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Soft delete'
        RESTORE = 'restore', 'Restore'
        STATUS = 'status', 'Status change'
        TRANSFER = 'transfer', 'Transfer'
        PUBLISH = 'publish', 'Publish'
        REVIEW = 'review', 'Review'
        EXPORT = 'export', 'Export'
        LOGIN = 'login', 'Login'
        LOGIN_FAILED = 'login_failed', 'Failed login'
        LOGOUT = 'logout', 'Logout'
        ACCOUNT = 'account', 'Account change'
        REPORT = 'report', 'Report delivery'

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                              related_name='audit_events')
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    model = models.CharField(max_length=100, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.timestamp:%Y-%m-%d %H:%M} {self.actor} {self.action} {self.model} {self.object_repr}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError('Audit events cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Audit events cannot be deleted.')

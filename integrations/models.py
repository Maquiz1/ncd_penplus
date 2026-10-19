from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ExternalRecord(models.Model):
    """Maps a row in an external/legacy system to the record created from it.

    Makes imports re-runnable (rows already imported are skipped) and keeps every
    migrated record traceable to its source table and primary key.
    """

    source = models.CharField(max_length=50, db_index=True)
    table = models.CharField(max_length=64)
    source_id = models.CharField(max_length=64)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64)
    target = GenericForeignKey('content_type', 'object_id')
    imported_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'table', 'source_id', 'content_type'],
                                               name='unique_external_row_per_target_type')]
        indexes = [models.Index(fields=['content_type', 'object_id'])]

    def __str__(self):
        return f'{self.source}.{self.table}#{self.source_id} → {self.content_type.model} {self.object_id}'

from django.contrib import admin

from .models import ExternalRecord


@admin.register(ExternalRecord)
class ExternalRecordAdmin(admin.ModelAdmin):
    list_display = ('source', 'table', 'source_id', 'content_type', 'object_id', 'imported_at', 'notes')
    list_filter = ('source', 'table', 'content_type')
    search_fields = ('source_id', 'object_id')

    def has_change_permission(self, request, obj=None):
        return False

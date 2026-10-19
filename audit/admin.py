from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'actor', 'action', 'model', 'object_repr', 'reason')
    list_filter = ('action', 'model')
    search_fields = ('object_repr', 'object_id', 'actor__username', 'reason')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

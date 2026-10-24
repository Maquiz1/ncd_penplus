from django.contrib import admin

from .models import ExportJob


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'dataset_label', 'fmt', 'mode', 'deidentified', 'status', 'rows', 'downloads')
    list_filter = ('status', 'fmt', 'mode', 'deidentified')
    readonly_fields = [f.name for f in ExportJob._meta.fields]

    def has_add_permission(self, request):
        return False

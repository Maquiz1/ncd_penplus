from django.contrib import admin

from .models import DataIssue


@admin.register(DataIssue)
class DataIssueAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'code', 'patient', 'status', 'related_date')
    list_filter = ('status', 'category', 'code')
    search_fields = ('title', 'key', 'patient__patient_number')

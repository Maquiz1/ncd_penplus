from django.contrib import admin

from .models import ReportRun


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = ('report_type', 'period_start', 'recipient', 'status', 'sent_at')
    list_filter = ('status', 'report_type')

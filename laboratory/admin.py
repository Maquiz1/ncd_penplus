from django.contrib import admin

from .models import InvestigationOrder, LabCategory, LabOrder, LabPanel, LabTest


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'category', 'result_type', 'unit', 'reference_low', 'reference_high', 'is_active')
    list_filter = ('category', 'result_type', 'is_active')


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ('patient', 'test', 'status', 'ordered_at', 'result_value')
    list_filter = ('status', 'test')


@admin.register(InvestigationOrder)
class InvestigationOrderAdmin(admin.ModelAdmin):
    list_display = ('patient', 'modality', 'status', 'requested_at')
    list_filter = ('status', 'modality')


admin.site.register(LabCategory)


@admin.register(LabPanel)
class LabPanelAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active')
    filter_horizontal = ('tests',)

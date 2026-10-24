from django.contrib import admin

from .models import InvestigationField, InvestigationOrder, InvestigationType


class InvestigationFieldInline(admin.TabularInline):
    model = InvestigationField
    extra = 0


@admin.register(InvestigationType)
class InvestigationTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'modality', 'order', 'is_active')
    list_filter = ('modality', 'is_active')
    inlines = [InvestigationFieldInline]


@admin.register(InvestigationOrder)
class InvestigationOrderAdmin(admin.ModelAdmin):
    list_display = ('patient', 'investigation_type', 'modality', 'status', 'requested_at')
    list_filter = ('status', 'modality', 'investigation_type')

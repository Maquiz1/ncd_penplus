from django.contrib import admin

from .models import Patient, PatientFacilityAssignment, PatientIdentifier, PatientIdSequence, PatientTransfer


class IdentifierInline(admin.TabularInline):
    model = PatientIdentifier
    extra = 0


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('patient_number', 'first_name', 'last_name', 'sex', 'date_of_birth', 'current_facility', 'status')
    list_filter = ('status', 'sex', 'current_facility')
    search_fields = ('patient_number', 'first_name', 'last_name', 'phone')
    readonly_fields = ('patient_number', 'current_facility', 'vital_status', 'date_of_death')
    inlines = [IdentifierInline]


@admin.register(PatientTransfer)
class PatientTransferAdmin(admin.ModelAdmin):
    list_display = ('patient', 'from_facility', 'to_facility', 'request_date', 'status')
    list_filter = ('status',)


admin.site.register(PatientFacilityAssignment)
admin.site.register(PatientIdSequence)

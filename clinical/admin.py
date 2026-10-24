from django.contrib import admin

from .models import (ClinicalProgramme, Diagnosis, Disease, EnrollmentEvent, PreScreening, ProgrammeEnrollment,
                     Screening, ScreeningSession)


@admin.register(Disease)
class DiseaseAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active')


@admin.register(ClinicalProgramme)
class ClinicalProgrammeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'disease', 'is_active')
    list_filter = ('disease',)


@admin.register(Screening)
class ScreeningAdmin(admin.ModelAdmin):
    list_display = ('patient', 'disease', 'screening_date', 'source', 'result')
    list_filter = ('disease', 'result', 'source')


@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ('patient', 'disease', 'description', 'status', 'diagnosis_date')
    list_filter = ('disease', 'status')


@admin.register(ProgrammeEnrollment)
class ProgrammeEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'programme', 'facility', 'enrollment_date', 'status')
    list_filter = ('programme', 'status')


admin.site.register(PreScreening)


@admin.register(ScreeningSession)
class ScreeningSessionAdmin(admin.ModelAdmin):
    list_display = ('name', 'facility', 'session_date', 'source', 'place_name', 'is_closed')
    list_filter = ('source', 'is_closed', 'facility')


admin.site.register(EnrollmentEvent)

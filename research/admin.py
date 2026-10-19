from django.contrib import admin

from .models import ConsentRecord, Study, StudyEligibilityAssessment, StudyEnrollment


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'status', 'start_date', 'end_date')
    list_filter = ('status',)
    filter_horizontal = ('diseases', 'sites')


@admin.register(StudyEnrollment)
class StudyEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('participant_id', 'study', 'patient', 'status', 'enrollment_date')
    list_filter = ('study', 'status')


admin.site.register(ConsentRecord)
admin.site.register(StudyEligibilityAssessment)

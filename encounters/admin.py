from django.contrib import admin

from .models import Admission, Appointment, Encounter, FollowUpPlan, Vitals


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'facility', 'scheduled_date', 'stage', 'status', 'plan_version')
    list_filter = ('status', 'stage', 'facility')


@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ('patient', 'facility', 'encounter_date', 'stage', 'location_type', 'status')
    list_filter = ('status', 'stage', 'location_type')


admin.site.register(FollowUpPlan)
admin.site.register(Vitals)
admin.site.register(Admission)

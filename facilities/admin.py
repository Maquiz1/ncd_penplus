from django.contrib import admin

from .models import District, Facility, Region, StaffAssignment, Street, Ward, Zone


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')
    search_fields = ('name',)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ('name', 'zone', 'code')
    list_filter = ('zone',)
    search_fields = ('name',)


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ('name', 'region', 'code')
    list_filter = ('region__zone', 'region')
    search_fields = ('name',)


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'district')
    list_filter = ('district__region',)
    search_fields = ('name', 'district__name')


@admin.register(Street)
class StreetAdmin(admin.ModelAdmin):
    list_display = ('name', 'ward', 'code')
    search_fields = ('name', 'ward__name')
    autocomplete_fields = ('ward',)


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'facility_type', 'district', 'is_research_site', 'is_active')
    list_filter = ('facility_type', 'is_research_site', 'is_active', 'district__region')
    search_fields = ('name', 'code')


@admin.register(StaffAssignment)
class StaffAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'level', 'facility', 'district', 'region', 'zone', 'start_date', 'end_date', 'is_active')
    list_filter = ('level', 'is_active')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user', 'facility')

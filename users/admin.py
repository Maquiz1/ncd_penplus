from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from facilities.models import StaffAssignment

from .models import User


class StaffAssignmentInline(admin.TabularInline):
    model = StaffAssignment
    fk_name = 'user'
    extra = 0
    fields = ('level', 'facility', 'district', 'region', 'zone', 'start_date', 'end_date', 'is_active')


@admin.register(User)
class StaffUserAdmin(UserAdmin):
    list_display = ('username', 'first_name', 'last_name', 'email', 'job_title', 'is_active', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (('Staff profile', {'fields': ('phone', 'phone_verified', 'job_title')}),)
    inlines = [StaffAssignmentInline]

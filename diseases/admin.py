from django.contrib import admin

from .models import Condition


@admin.register(Condition)
class ConditionAdmin(admin.ModelAdmin):
    list_display = ('name', 'disease', 'indicator_code', 'is_default', 'is_active')
    list_filter = ('disease', 'is_active')
    filter_horizontal = ('excludes',)

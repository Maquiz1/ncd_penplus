from django.contrib import admin

from .models import EmailLog, SiteConfiguration, SmsMessage


@admin.register(SmsMessage)
class SmsMessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'patient', 'purpose', 'status', 'backend', 'attempts', 'sent_at')
    list_filter = ('status', 'purpose', 'backend')
    readonly_fields = ('provider_reference', 'error', 'attempts', 'sent_at')


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ('name', 'site_url', 'from_name', 'from_email', 'is_active')
    list_filter = ('is_active',)


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'recipient', 'purpose', 'subject', 'status', 'backend', 'sent_by')
    list_filter = ('status', 'purpose', 'backend')
    search_fields = ('recipient', 'subject', 'user__username')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

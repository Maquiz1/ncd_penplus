from django.contrib import admin

from .models import FormDefinition, FormField, FormSection, FormSubmission, FormVersion


@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'applicability', 'is_active')
    list_filter = ('applicability', 'is_active')


@admin.register(FormVersion)
class FormVersionAdmin(admin.ModelAdmin):
    list_display = ('definition', 'number', 'status', 'published_at')
    list_filter = ('status',)


@admin.register(FormSubmission)
class FormSubmissionAdmin(admin.ModelAdmin):
    list_display = ('version', 'patient', 'encounter', 'status', 'submitted_at')
    list_filter = ('status', 'version__definition')
    readonly_fields = ('data',)


admin.site.register(FormSection)
admin.site.register(FormField)

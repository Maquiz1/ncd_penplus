from django.contrib import admin

from .models import Dispensing, Medication, Prescription, StockBatch, StockMovement


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ('name', 'strength', 'dosage_form', 'dispensing_unit', 'reorder_level', 'is_active')
    list_filter = ('dosage_form', 'is_active')
    search_fields = ('name', 'code')


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ('medication', 'facility', 'batch_number', 'expiry_date', 'quantity_on_hand')
    list_filter = ('facility', 'medication')
    readonly_fields = ('quantity_on_hand',)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'batch', 'movement_type', 'quantity', 'balance_after', 'performed_by', 'reason')
    list_filter = ('movement_type',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ('patient', 'medication', 'dose', 'frequency', 'quantity', 'status', 'prescribed_at')
    list_filter = ('status', 'facility')


@admin.register(Dispensing)
class DispensingAdmin(admin.ModelAdmin):
    list_display = ('prescription', 'batch', 'quantity', 'dispensed_by', 'dispensed_at')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

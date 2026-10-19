import datetime

from django.db.models import Count, Q, Sum
from django.utils import timezone

from clinical.models import ProgrammeEnrollment, Screening
from crf.models import FormSubmission
from encounters.models import Admission, Appointment, Encounter
from facilities.scope import facilities_for_user, scope_queryset
from laboratory.models import InvestigationOrder, LabOrder
from patients.models import Patient, PatientTransfer
from pharmacy.models import Dispensing, Prescription, StockBatch
from pharmacy.services import low_stock_rows
from research.models import StudyEnrollment


def scoped(model, user, field='patient__current_facility'):
    return scope_queryset(model.objects.all(), user, field)


def dashboard_stats(user):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    patients = Patient.objects.in_scope(user)
    appointments = scoped(Appointment, user)
    return {
        'patients_total': patients.count(),
        'patients_new_month': patients.filter(registration_date__gte=month_start).count(),
        'programme_active': scoped(ProgrammeEnrollment, user).filter(status='active').count(),
        'study_active': scoped(StudyEnrollment, user).filter(status='active').count(),
        'appointments_today': appointments.filter(scheduled_date=today).exclude(status='cancelled').count(),
        'appointments_overdue': appointments.filter(scheduled_date__lt=today,
                                                    status__in=['scheduled', 'confirmed', 'rescheduled']).count(),
        'appointments_reconcile': appointments.filter(status='expected').count(),
        'encounters_open': scoped(Encounter, user).filter(status__in=['open', 'in_progress', 'incomplete']).count(),
        'labs_pending': scoped(LabOrder, user).filter(status__in=['ordered', 'collected']).count(),
        'labs_to_review': scoped(LabOrder, user).filter(status='resulted').count(),
        'investigations_pending': scoped(InvestigationOrder, user).filter(status__in=['requested', 'scheduled']).count(),
        'forms_to_review': scoped(FormSubmission, user).filter(status__in=['submitted', 'under_review']).count(),
        'inpatients': scoped(Admission, user).filter(discharged_at__isnull=True).count(),
        'prescriptions_pending': scope_queryset(Prescription.objects.filter(status__in=Prescription.OPEN_STATUSES),
                                                user, 'facility').count(),
        'low_stock': len(low_stock_rows(list(facilities_for_user(user)))),
        'expired_batches': scope_queryset(StockBatch.objects.filter(quantity_on_hand__gt=0, expiry_date__lt=today),
                                          user, 'facility').count(),
        'transfers_incoming': PatientTransfer.objects.filter(
            to_facility__in=facilities_for_user(user), status='sent').count(),
        'programmes_by_disease': list(
            scoped(ProgrammeEnrollment, user).filter(status='active')
            .values('programme__disease__name').annotate(n=Count('id')).order_by('-n')),
        'screening_by_disease': list(
            scoped(Screening, user).values('disease__name')
            .annotate(total=Count('id'), positive=Count('id', filter=Q(result='positive'))).order_by('disease__name')),
    }


def period_report(user, start, end):
    """Enrollment, transfer, follow-up and data-quality figures for a period, within the user's scope."""
    facilities = facilities_for_user(user)
    programme = scoped(ProgrammeEnrollment, user).filter(enrollment_date__range=(start, end))
    research = scoped(StudyEnrollment, user).filter(enrollment_date__range=(start, end))
    transfers = PatientTransfer.objects.filter(effective_date__range=(start, end), status='received')
    appointments = scoped(Appointment, user).filter(scheduled_date__range=(start, end))
    screenings = scoped(Screening, user).filter(screening_date__range=(start, end))
    labs = scoped(LabOrder, user).filter(ordered_at__date__range=(start, end))
    return {
        'start': start,
        'end': end,
        'new_patients': Patient.objects.in_scope(user).filter(registration_date__range=(start, end)).count(),
        'programme_total': programme.count(),
        'programme_unique_patients': programme.values('patient').distinct().count(),
        'programme_by_disease_facility': list(
            programme.values('programme__disease__name', 'facility__name').annotate(n=Count('id'))
            .order_by('programme__disease__name', 'facility__name')),
        'research_total': research.count(),
        'research_by_study_facility': list(
            research.values('study__code', 'facility__name').annotate(n=Count('id')).order_by('study__code')),
        'transfers_in': transfers.filter(to_facility__in=facilities).count(),
        'transfers_out': transfers.filter(from_facility__in=facilities).count(),
        'appointments_by_status': dict(appointments.values_list('status').annotate(n=Count('id'))),
        'screenings_by_disease': list(
            screenings.values('disease__name', 'source').annotate(
                total=Count('id'), positive=Count('id', filter=Q(result='positive'))).order_by('disease__name')),
        'incomplete_encounters': scoped(Encounter, user).filter(
            encounter_date__date__range=(start, end), status__in=['open', 'in_progress', 'incomplete']).count(),
        'forms_awaiting_review': scoped(FormSubmission, user).filter(status__in=['submitted', 'under_review']).count(),
        'labs_ordered': labs.count(),
        'labs_pending': labs.filter(status__in=['ordered', 'collected']).count(),
        'missing_phone': Patient.objects.in_scope(user).filter(phone='').count(),
        'dispensed_by_medication': list(
            scope_queryset(Dispensing.objects.filter(dispensed_at__date__range=(start, end)), user, 'batch__facility')
            .values('batch__medication__name', 'batch__medication__strength', 'batch__medication__dispensing_unit')
            .annotate(total=Sum('quantity'), patients=Count('prescription__patient', distinct=True))
            .order_by('batch__medication__name')),
        'prescriptions_written': scope_queryset(Prescription.objects.filter(prescribed_at__date__range=(start, end)),
                                                user, 'facility').count(),
        'stock_expiring': list(
            scope_queryset(StockBatch.objects.filter(quantity_on_hand__gt=0, expiry_date__lte=end + datetime.timedelta(days=90)),
                           user, 'facility').select_related('medication', 'facility').order_by('expiry_date')[:50]),
    }

import datetime

from django.db.models import Count, Q, Sum
from django.utils import timezone

from clinical.models import ProgrammeEnrollment, Screening
from crf.models import FormSubmission
from encounters.models import Admission, Appointment, Encounter
from facilities.scope import facilities_for_user, scope_queryset
from issues.models import DataIssue
from investigations.models import InvestigationOrder
from laboratory.models import LabOrder
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
        'programme_active': scoped(ProgrammeEnrollment, user).filter(status='active', programme__disease__is_active=True).count(),
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
        'data_issues': DataIssue.objects.in_scope(user).open().count(),
        'transfers_incoming': PatientTransfer.objects.filter(
            to_facility__in=facilities_for_user(user), status='sent').count(),
        'programmes_by_disease': list(
            scoped(ProgrammeEnrollment, user).filter(status='active', programme__disease__is_active=True)
            .values('programme__disease__name').annotate(n=Count('id')).order_by('-n')),
        'screening_by_disease': list(
            scoped(Screening, user).filter(disease__is_active=True).values('disease__name')
            .annotate(total=Count('id'), positive=Count('id', filter=Q(result='positive'))).order_by('disease__name')),
    }


def period_report(user, start, end, facilities=None):
    """Enrollment, transfer, follow-up and data-quality figures for a period, within the user's scope
    (optionally narrowed to some of the user's facilities)."""
    narrowed = facilities is not None
    facilities = facilities if narrowed else facilities_for_user(user)

    def sc(model, field='patient__current_facility'):
        qs = scoped(model, user, field)
        return qs.filter(**{f'{field}__in': facilities}) if narrowed else qs

    def sq(qs, field):
        qs = scope_queryset(qs, user, field)
        return qs.filter(**{f'{field}__in': facilities}) if narrowed else qs

    patients = Patient.objects.in_scope(user)
    if narrowed:
        patients = patients.filter(current_facility__in=facilities)
    programme = sc(ProgrammeEnrollment).filter(enrollment_date__range=(start, end), programme__disease__is_active=True)
    research = sc(StudyEnrollment).filter(enrollment_date__range=(start, end))
    transfers = PatientTransfer.objects.filter(effective_date__range=(start, end), status='received')
    appointments = sc(Appointment).filter(scheduled_date__range=(start, end))
    screenings = sc(Screening).filter(screening_date__range=(start, end), disease__is_active=True)
    labs = sc(LabOrder).filter(ordered_at__date__range=(start, end))
    return {
        'start': start,
        'end': end,
        'new_patients': patients.filter(registration_date__range=(start, end)).count(),
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
        'incomplete_encounters': sc(Encounter).filter(
            encounter_date__date__range=(start, end), status__in=['open', 'in_progress', 'incomplete']).count(),
        'forms_awaiting_review': sc(FormSubmission).filter(status__in=['submitted', 'under_review']).count(),
        'labs_ordered': labs.count(),
        'labs_pending': labs.filter(status__in=['ordered', 'collected']).count(),
        'missing_phone': patients.filter(phone='').count(),
        'dispensed_by_medication': list(
            sq(Dispensing.objects.filter(dispensed_at__date__range=(start, end)), 'batch__facility')
            .values('batch__medication__name', 'batch__medication__strength', 'batch__medication__dispensing_unit')
            .annotate(total=Sum('quantity'), patients=Count('prescription__patient', distinct=True))
            .order_by('batch__medication__name')),
        'prescriptions_written': sq(Prescription.objects.filter(prescribed_at__date__range=(start, end)),
                                    'facility').count(),
        'stock_expiring': list(
            sq(StockBatch.objects.filter(quantity_on_hand__gt=0, expiry_date__lte=end + datetime.timedelta(days=90)),
               'facility').select_related('medication', 'facility').order_by('expiry_date')[:50]),
    }

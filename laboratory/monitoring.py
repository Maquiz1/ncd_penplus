"""Routine monitoring tests (e.g. HbA1c every 3 months for diabetes): last result, when it is due, and pending orders."""
import calendar

from django.conf import settings
from django.utils import timezone

from core.utils import add_months

from .models import LabOrder, MonitoringGap

# test code -> (disease code, interval in months)
MONITORED = {'hba1c': ('diabetes', settings.HBA1C_INTERVAL_MONTHS)}
RESULTED = ('resulted', 'reviewed')
PENDING = ('ordered', 'collected')

STATES = [('overdue', 'Overdue'), ('this_month', 'Due this month'), ('next_month', 'Due next month'),
          ('ok', 'Up to date')]


def month_end(date):
    return date.replace(day=calendar.monthrange(date.year, date.month)[1])


def compute(code, enrollment_date, last=None, pending=None, gap=None, today=None):
    """Status of one patient from their first active enrolment, last result, open order and latest "not done" record.

    'due' counts from the last result (or enrolment when there is none). A "not done" record for this cycle
    explains the gap and moves the due date to HBA1C_RETRY_MONTHS after it was recorded."""
    months = MONITORED[code][1]
    today = today or timezone.localdate()
    last_date = timezone.localtime(last.resulted_at).date() if last else None
    due = add_months(last_date or enrollment_date, months)
    if gap is not None and gap.due_date < due:
        gap = None  # explains an earlier cycle that a result has since closed
    if gap is not None:
        due = max(due, add_months(gap.recorded_on, settings.HBA1C_RETRY_MONTHS))
    if today > due:
        state = 'overdue'
    elif due <= month_end(today):
        state = 'this_month'
    elif due <= month_end(add_months(today.replace(day=1), 1)):
        state = 'next_month'
    else:
        state = 'ok'
    return {'test': last.test.name if last else code.upper(), 'code': code, 'last': last, 'last_date': last_date,
            'days': (today - last_date).days if last_date else None, 'due': due, 'overdue': state == 'overdue',
            'state': state, 'pending': pending, 'gap': gap, 'months': months,
            'days_overdue': (today - due).days if state == 'overdue' else None}


def monitoring_status(patient, code='hba1c', today=None, enrollment_date=None):
    """Monitoring status of one patient enrolled in the test's disease, else None."""
    disease = MONITORED[code][0]
    if enrollment_date is None:
        enrollment = patient.programme_enrollments.filter(status='active', programme__disease__code=disease) \
            .order_by('enrollment_date').first()
        if enrollment is None:
            return None
        enrollment_date = enrollment.enrollment_date
    orders = LabOrder.objects.filter(patient=patient, test__code=code).select_related('test')
    last = orders.filter(status__in=RESULTED, resulted_at__isnull=False).order_by('-resulted_at').first()
    pending = orders.filter(status__in=PENDING).order_by('-ordered_at').first()
    gap = MonitoringGap.objects.filter(patient=patient, test__code=code).order_by('-due_date', '-pk').first()
    return compute(code, enrollment_date, last, pending, gap, today)


def monitoring_list(patients, code='hba1c', today=None):
    """[(patient, status)] for active, living patients enrolled in the test's disease, with a few queries in total."""
    from clinical.models import ProgrammeEnrollment

    disease = MONITORED[code][0]
    enrolled = {}
    for e in (ProgrammeEnrollment.objects.filter(status='active', programme__disease__code=disease, patient__in=patients)
              .order_by('enrollment_date').values('patient_id', 'enrollment_date')):
        enrolled.setdefault(e['patient_id'], e['enrollment_date'])
    last, pending, gaps = {}, {}, {}
    orders = LabOrder.objects.filter(patient_id__in=enrolled, test__code=code).select_related('test')
    for o in orders.filter(status__in=RESULTED, resulted_at__isnull=False).order_by('resulted_at'):
        last[o.patient_id] = o
    for o in orders.filter(status__in=PENDING).order_by('ordered_at'):
        pending[o.patient_id] = o
    for g in MonitoringGap.objects.filter(patient_id__in=enrolled, test__code=code).order_by('due_date', 'pk'):
        gaps[g.patient_id] = g
    rows = []
    for patient in patients.filter(pk__in=enrolled, vital_status='alive').select_related('current_facility'):
        pid = patient.pk
        rows.append((patient, compute(code, enrolled[pid], last.get(pid), pending.get(pid), gaps.get(pid), today)))
    return rows

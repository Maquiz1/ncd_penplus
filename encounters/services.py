from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event
from core.utils import add_months

from .models import Appointment


def was_enrolled(patient):
    return patient.programme_enrollments.exists()


def baseline_visit(patient, facility, request):
    """The one baseline visit holding everything from pre-screening to enrollment, opened the first time it is needed.
    None once the subject has been enrolled: later records belong to follow-up visits."""
    from .models import Encounter

    if was_enrolled(patient):
        return None
    visit = patient.encounters.filter(stage='baseline').exclude(status='cancelled').first()
    if visit is None:
        visit = Encounter.objects.create(patient=patient, facility=facility, stage='baseline', staff=request.user,
                                         created_by=request.user)
        log_event(request=request, action=AuditEvent.Action.CREATE, obj=visit, reason='Baseline visit opened')
    return visit


def current_visit(patient):
    """The visit new records belong to: the open baseline before enrollment, otherwise today's visit."""
    baseline = None
    if not was_enrolled(patient):
        baseline = patient.encounters.filter(stage='baseline').exclude(status='cancelled').first()
    return baseline or todays_encounter(patient)


def close_baseline(patient, request, enrollment=None, stage=None):
    """End the baseline visit at enrollment, or as screening only when no disease was diagnosed."""
    visit = patient.encounters.filter(stage='baseline', status__in=['open', 'in_progress']).first()
    if visit is None:
        return
    visit.status = 'completed'
    visit.programme_enrollment = visit.programme_enrollment or enrollment
    visit.stage = stage or visit.stage
    visit.save()
    log_event(request=request, action=AuditEvent.Action.STATUS, obj=visit, changes={'status': 'completed', 'stage': visit.stage})


def planned_dates(plan):
    """Visit dates from the anchor at the plan interval, bounded by count and end date."""
    today = timezone.localdate()
    dates = []
    sequence = 0
    future = 0
    while future < plan.visit_count:
        sequence += 1
        date = add_months(plan.anchor_date, plan.interval_months * sequence)
        if plan.end_date and date > plan.end_date:
            break
        dates.append((sequence, date))
        if date >= today:
            future += 1
        if sequence > 600:
            break
    return dates


@transaction.atomic
def generate_visits(plan, user):
    """Create visits for the plan's current version without duplicating existing ones.

    Past dates become 'expected' visits for staff to reconcile against source
    records; they are never automatically marked missed.
    """
    today = timezone.localdate()
    existing = set(Appointment.objects.filter(plan=plan, plan_version=plan.version).values_list('sequence', flat=True))
    kept_dates = set(Appointment.objects.filter(plan=plan).exclude(status='cancelled').values_list('scheduled_date', flat=True))
    created = 0
    for sequence, date in planned_dates(plan):
        if sequence in existing or date in kept_dates:
            continue
        if date < today and plan.version > 1:
            # A revised schedule only changes the future; history stays as it was.
            continue
        Appointment.objects.create(
            patient=plan.patient, facility=plan.facility, scheduled_date=date, stage='follow_up',
            status=Appointment.Status.EXPECTED if date < today else Appointment.Status.SCHEDULED,
            programme_enrollment=plan.programme_enrollment, study_enrollment=plan.study_enrollment,
            plan=plan, plan_version=plan.version, sequence=sequence, created_by=user, origin='schedule',
        )
        created += 1
    return created


@transaction.atomic
def revise_plan(plan, user, reason, request=None):
    """Apply a changed schedule: bump the version, cancel only future unstarted visits, regenerate."""
    today = timezone.localdate()
    plan.version += 1
    plan.save(update_fields=['version', 'updated_at'])
    cancelled = Appointment.objects.filter(
        plan=plan, scheduled_date__gte=today, status__in=['scheduled', 'confirmed', 'rescheduled'],
        encounters__isnull=True,
    ).update(status=Appointment.Status.CANCELLED, reason_for_change=f'Superseded by schedule v{plan.version}: {reason}'[:255])
    created = generate_visits(plan, user) if plan.generation == 'automatic' else 0
    log_event(actor=user, request=request, action=AuditEvent.Action.UPDATE, obj=plan, reason=reason,
              changes={'version': plan.version, 'future_visits_cancelled': cancelled, 'visits_created': created})
    return cancelled, created


def todays_encounter(patient):
    """The subject's visit today (latest one), so orders made from the subject page are linked to it."""
    import datetime
    start = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time.min))
    return (patient.encounters.exclude(status='cancelled')
            .filter(encounter_date__gte=start, encounter_date__lt=start + datetime.timedelta(days=1))
            .order_by('-encounter_date').first())

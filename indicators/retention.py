"""Retention & returns: clinic indicators (not on the official PEN-Plus list) about subjects who left care or missed
visits, and what happened next. Every number is a set of subjects, so each can be listed (drill-down).

1. Returned from LTFU in the period: still active, or inactive again.
2. Three or more missed visits in a row (the third missed in the period): came back before LTFU, became LTFU then
   returned, became LTFU and not back, still missing (not LTFU yet), or left care another way.
3. Transferred out of PEN-Plus and came back in the period: still active or not.
4. Referred to another hospital in the period: back, or not back yet; by hospital.
"""
from collections import OrderedDict, defaultdict

from clinical.models import EnrollmentEvent, ProgrammeEnrollment
from encounters.models import Appointment, Referral

ACTIVE = ('active', 'pending')


def _scope(filters):
    """Subjects at the chosen sites, in a clinic of the chosen diseases."""
    diseases = [filters.disease] if filters.disease else list(filters.diseases)
    enrollments = ProgrammeEnrollment.objects.filter(patient__current_facility__in=filters.facilities,
                                                     patient__is_deleted=False, programme__disease__in=diseases)
    return enrollments, set(enrollments.values_list('patient_id', flat=True))


def _active(patient_ids):
    return set(ProgrammeEnrollment.objects.filter(patient_id__in=patient_ids, status__in=ACTIVE)
               .values_list('patient_id', flat=True))


def returned_from_ltfu(filters, enrollments):
    events = EnrollmentEvent.objects.filter(enrollment__in=enrollments, kind='return', from_status='ltfu',
                                            event_date__range=(filters.start, filters.end))
    returned = set(events.values_list('enrollment__patient_id', flat=True))
    active = _active(returned)
    return OrderedDict([('returned', returned), ('still_active', returned & active),
                        ('inactive_again', returned - active)])


def missed_three(filters, patients):
    """Runs of 3+ consecutive missed appointments whose third miss falls in the period, and what came next."""
    rows = (Appointment.objects.filter(patient_id__in=patients, status__in=['missed', 'attended'],
                                       scheduled_date__lte=filters.end)
            .order_by('patient_id', 'scheduled_date', 'pk').values_list('patient_id', 'scheduled_date', 'status'))
    first_run = {}
    run, current = [], None
    for patient_id, date, status in list(rows) + [(None, None, None)]:
        if patient_id != current:
            run, current = [], patient_id
        if status == 'missed':
            run.append(date)
            if len(run) == 3 and patient_id not in first_run and filters.start <= date <= filters.end:
                first_run[patient_id] = (run[0], date)   # the run starts, and its third miss
        else:
            run = []
    flagged = set(first_run)
    attended_after = set()
    for patient_id, (_start, third) in first_run.items():
        if Appointment.objects.filter(patient_id=patient_id, status='attended', scheduled_date__gt=third).exists() \
                or EnrollmentEvent.objects.filter(enrollment__patient_id=patient_id, kind='return',
                                                  event_date__gt=third).exists():
            attended_after.add(patient_id)
    became_ltfu = set(EnrollmentEvent.objects.filter(enrollment__patient_id__in=flagged, to_status='ltfu')
                      .values_list('enrollment__patient_id', flat=True))
    # the LTFU must come after the run started
    became_ltfu = {pid for pid in became_ltfu if EnrollmentEvent.objects.filter(
        enrollment__patient_id=pid, to_status='ltfu', event_date__gte=first_run[pid][0]).exists()}
    statuses = defaultdict(set)
    for pid, status in ProgrammeEnrollment.objects.filter(patient_id__in=flagged).values_list('patient_id', 'status'):
        statuses[pid].add(status)
    active = {pid for pid, s in statuses.items() if s & set(ACTIVE)}
    came_back = (attended_after - became_ltfu) & active
    ltfu_returned = became_ltfu & attended_after
    ltfu_not_back = became_ltfu - attended_after
    still_missing = (flagged - attended_after - became_ltfu) & active
    other = flagged - came_back - ltfu_returned - ltfu_not_back - still_missing
    return OrderedDict([('missed_three', flagged), ('came_back', came_back), ('ltfu_returned', ltfu_returned),
                        ('ltfu_not_back', ltfu_not_back), ('still_missing', still_missing), ('left_other', other)])


def transfer_returns(filters, enrollments):
    left = EnrollmentEvent.objects.filter(enrollment__in=enrollments, to_status='transferred_out')
    back = EnrollmentEvent.objects.filter(enrollment__in=left.values('enrollment'), kind__in=['return', 'transfer_in'],
                                          from_status='transferred_out',
                                          event_date__range=(filters.start, filters.end))
    returned = set(back.values_list('enrollment__patient_id', flat=True))
    active = _active(returned)
    return OrderedDict([('transfer_returned', returned), ('transfer_still_active', returned & active)])


def referrals(filters, patients):
    rows = Referral.objects.filter(patient_id__in=patients, referral_date__range=(filters.start, filters.end)) \
        .select_related('to_facility')
    referred, back, not_back = set(), set(), set()
    by_hospital = defaultdict(lambda: {'referred': set(), 'back': set()})
    for r in rows:
        referred.add(r.patient_id)
        hospital = by_hospital[r.destination or 'Not recorded']
        hospital['referred'].add(r.patient_id)
        if r.returned_on:
            back.add(r.patient_id)
            hospital['back'].add(r.patient_id)
        else:
            not_back.add(r.patient_id)
    return OrderedDict([('referred', referred), ('referral_back', back), ('referral_not_back', not_back - back)]), \
        dict(sorted(by_hospital.items()))


LABELS = {
    'returned': 'Returned from LTFU', 'still_active': 'still active', 'inactive_again': 'inactive again',
    'missed_three': 'Missed 3 or more visits in a row', 'came_back': 'came back before becoming LTFU',
    'ltfu_returned': 'became LTFU, then returned', 'ltfu_not_back': 'became LTFU, not back',
    'still_missing': 'still missing, not LTFU yet', 'left_other': 'left care another way (died, transferred, withdrew)',
    'transfer_returned': 'Transferred out of PEN-Plus and came back', 'transfer_still_active': 'still active after coming back',
    'referred': 'Referred to another hospital', 'referral_back': 'came back', 'referral_not_back': 'not back yet',
}


def retention_indicators(filters):
    enrollments, patients = _scope(filters)
    referral_sets, by_hospital = referrals(filters, patients)
    sets = OrderedDict()
    for part in (returned_from_ltfu(filters, enrollments), missed_three(filters, patients),
                 transfer_returns(filters, enrollments), referral_sets):
        sets.update(part)
    return sets, by_hospital

"""De-identified subject summary: everything a PI, coordinator or specialist needs, without names or contacts."""
import datetime
import json
import re
from functools import lru_cache
from pathlib import Path

from django.utils import timezone

from crf.models import FormField, FormSubmission

# EDCS forms shown on the summary, by tab.
ABOUT_FORM = 'edcs-clients'
HISTORY_FORMS = ['edcs-history']
SYMPTOM_FORMS = ['edcs-symptoms', 'cardiac-assessment', 'diabetes-assessment', 'scd-assessment']
# Free text that could name or locate a person is never shown on the summary.
HIDDEN_KEY = re.compile(r'name|phone|address|comment|remark|note|gurdian|guardian|relation_patient')
EMPTY = {'', None, 'none', 'n/a', 'na', '-', '0000-00-00'}
NEGATIVE = {'no', 'never', 'none', 'normal', 'unknown', 'unk', 'unsure', 'not done', '0', 'absent', 'clear', 'euvolemic',
            'cannot determine', 'unable to determine'}
# 98 / 99 mean "not done" / "missing" in EDCS lab boxes.
LAB_KEY = re.compile(r'hba1c|fasting|random_fs|glucose')
LAB_MISSING = {'98', '99', '98.0', '99.0', '0', '00', '0.0'}


@lru_cache(maxsize=1)
def edcs_labels():
    """Code labels of EDCS radio questions, taken from the PenPlus data dictionary (v1, 2024-03-17)."""
    path = Path(__file__).resolve().parent.parent / 'integrations' / 'edcs_choice_labels.json'
    return json.loads(path.read_text(encoding='utf-8'))


class Answers:
    """Labelled answers of CRF submissions, with field definitions loaded once per form version."""

    def __init__(self):
        self.fields = {}

    def for_version(self, version_id):
        if version_id not in self.fields:
            self.fields[version_id] = list(FormField.objects.filter(section__version_id=version_id)
                                           .order_by('section__order', 'order'))
        return self.fields[version_id]

    def answers(self, submission, positive_only=False):
        """[(label, value)] for answered, shareable fields; positive_only drops No / Never / Normal answers."""
        rows = []
        for field in self.for_version(submission.version_id):
            if field.field_type == FormField.FieldType.TEXTAREA or HIDDEN_KEY.search(field.key):
                continue
            raw = submission.data.get(field.key)
            if isinstance(raw, list):
                raw = ', '.join(str(v) for v in raw)
            raw = '' if raw is None else str(raw).strip()
            if raw.lower() in EMPTY:
                continue
            if LAB_KEY.search(field.key) and raw in LAB_MISSING:
                continue
            value = raw
            labels = dict(field.choices()) or edcs_labels().get(field.key, {})
            if labels and field.field_type != FormField.FieldType.YESNO:
                value = ', '.join(labels.get(v.strip(), v.strip()) for v in raw.split(','))
            elif field.field_type == FormField.FieldType.YESNO:
                value = {'true': 'Yes', '1': 'Yes', 'yes': 'Yes', 'false': 'No', '0': 'No', 'no': 'No'}.get(raw.lower(), raw)
            if positive_only and value.strip().lower() in NEGATIVE:
                continue
            rows.append((field.label, value))
        return rows


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _submissions(patient, codes):
    return (FormSubmission.objects.filter(patient=patient, version__definition__code__in=codes)
            .select_related('version__definition', 'encounter').order_by('-created_at'))


def visit_stats(patient):
    today = timezone.localdate()
    appointments = patient.appointments.exclude(status='cancelled')
    due = appointments.filter(scheduled_date__lte=today)
    attended = due.filter(status='attended').count()
    expected = due.count()
    nxt = appointments.filter(status__in=['scheduled', 'confirmed', 'rescheduled'], scheduled_date__gte=today) \
        .order_by('scheduled_date').first()
    return {
        'expected': expected, 'attended': attended, 'missed': due.filter(status='missed').count(),
        'upcoming': appointments.filter(status__in=['scheduled', 'confirmed', 'rescheduled'],
                                        scheduled_date__gt=today).count(),
        'attendance': round(100 * attended / expected) if expected else None,
        'visits': patient.encounters.exclude(status='cancelled').count(),
        'next': nxt.scheduled_date if nxt else None,
    }


def subject_summary(patient):
    reader = Answers()
    about = _submissions(patient, [ABOUT_FORM]).first()
    about_rows = dict(reader.answers(about)) if about else {}

    history = []
    for s in _submissions(patient, HISTORY_FORMS)[:5]:
        answers = reader.answers(s, positive_only=True)
        # The form's own visit date is the date of the history, not the date the record was entered.
        when = _date(s.data.get('visit_date')) or (s.encounter.encounter_date if s.encounter else s.created_at)
        history.append({'date': when, 'form': s.version.definition.name,
                        'answers': [(label, value) for label, value in answers if label != 'Date of Visit']})

    encounters = list(patient.encounters.exclude(status='cancelled').select_related('facility')
                      .order_by('-encounter_date')[:20])
    by_encounter = {}
    for s in _submissions(patient, SYMPTOM_FORMS).filter(encounter__in=encounters):
        # the visit date is already on the timeline
        by_encounter.setdefault(s.encounter_id, []).extend(
            (label, value) for label, value in reader.answers(s, positive_only=True) if 'date' not in label.lower())
    visits = [{'encounter': e, 'findings': by_encounter.get(e.pk, [])} for e in encounters]

    from encounters.models import Vitals
    vitals = list(Vitals.objects.filter(encounter__patient=patient).select_related('encounter')
                  .order_by('-taken_at')[:24])

    return {
        'about': {
            'education': about_rows.get('Education level', ''),
            'employment': about_rows.get('Employment status', ''),
            'exposure': '' if about_rows.get('Occupational exposures', '').lower() in NEGATIVE
            else about_rows.get('Occupational exposures', ''),
            'district': patient.residence_district,
        },
        'programmes': patient.programme_enrollments.select_related('programme__disease', 'facility')
        .order_by('-enrollment_date'),
        'diagnoses': patient.diagnoses.exclude(status='ruled_out').select_related('disease', 'condition')
        .order_by('-diagnosis_date'),
        'stats': visit_stats(patient),
        'history': history,
        'visits': visits,
        'vitals': vitals,
        'labs': patient.lab_orders.filter(status__in=['resulted', 'reviewed']).select_related('test')
        .order_by('-resulted_at', '-ordered_at')[:40],
        'investigations': patient.investigations.select_related('investigation_type').order_by('-requested_at')[:20],
        'prescriptions': patient.prescriptions.select_related('medication').order_by('-prescribed_at')[:20],
        'medication_records': patient.medication_records.order_by('-record_date')[:40],
        'admissions': patient.admissions.select_related('facility').order_by('-admitted_at'),
    }

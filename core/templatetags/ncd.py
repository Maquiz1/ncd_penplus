from django import template
from django.utils.html import format_html

register = template.Library()

STATUS_COLOURS = {
    'success': {'active', 'confirmed', 'completed', 'attended', 'received', 'verified', 'resulted', 'reviewed',
                'positive', 'eligible', 'consented', 'enrolled', 'published', 'discharged', 'yes', 'alive'},
    'primary': {'scheduled', 'open', 'in_progress', 'submitted', 'ordered', 'requested', 'approved', 'sent',
                'collected', 'processing', 'admitted', 'draft', 'pending'},
    'warning': {'rescheduled', 'incomplete', 'under_review', 'provisional', 'inconclusive', 'expected',
                'returned', 'transferred', 'transferred_out', 'ltfu'},
    'danger': {'missed', 'cancelled', 'rejected', 'withdrawn', 'deceased', 'declined', 'ineligible',
               'ruled_out', 'failed', 'inactive', 'retired', 'no', 'abnormal'},
    'secondary': {'negative', 'not_applicable', 'other'},
}


@register.simple_tag
def status_badge(value, label=None):
    colour = next((c for c, values in STATUS_COLOURS.items() if value in values), 'secondary')
    return format_html('<span class="badge text-bg-{}">{}</span>', colour, label or str(value).replace('_', ' ').title())


@register.simple_tag(takes_context=True)
def patient_name(context, patient):
    """Show the patient's name only to users allowed to see identity fields."""
    if patient is None:
        return ''
    if context.get('can_view_identity'):
        return patient.full_name
    return format_html('<span class="text-body-secondary fst-italic">Identity restricted</span>')


@register.simple_tag
def old_patient_id(patient, prefix='Old ID'):
    """The full old EDCS study ID (or the old EDCS number) under a patient number; empty for new patients."""
    old = patient.old_study_id or patient.old_id if patient else ''
    if not old:
        return ''
    return format_html('<div class="small text-body-secondary text-nowrap">{} {}</div>', prefix, old)


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None


@register.simple_tag
def monitoring_for(patient, code='hba1c'):
    """Last result, due date and pending order of a routine monitoring test (None when not applicable)."""
    from laboratory.monitoring import monitoring_status
    return monitoring_status(patient, code)


@register.simple_tag
def diagnosis_prompts_for(patient):
    """Open "confirm the diagnosis" prompts for the patient banner."""
    from issues.detectors import DX_CODES
    from issues.models import DataIssue
    return list(DataIssue.objects.open().filter(patient=patient, code__in=DX_CODES))

from celery import shared_task
from django.contrib.auth import get_user_model

from .services import apply_ltfu_rule


@shared_task(name='clinical.apply_ltfu_rule')
def apply_ltfu_rule_task(months=None):
    """Move enrollments to default / LTFU after LTFU_MONTHS without an attended visit (care and research)."""
    system, _ = get_user_model().objects.get_or_create(
        username='system', defaults={'first_name': 'System', 'last_name': 'rules', 'is_active': False})
    return {'became_ltfu': len(apply_ltfu_rule(system, months=months))}

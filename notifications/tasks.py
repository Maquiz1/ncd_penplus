from celery import shared_task

from .services import process_queue, queue_reminders


@shared_task(name='notifications.send_sms')
def send_sms(reminders=True, limit=500):
    """Queue appointment reminders (day before / same day) and send queued SMS."""
    queued = queue_reminders() if reminders else 0
    return {'reminders_queued': queued, **process_queue(limit=limit)}


@shared_task(name='notifications.collect_delivery_reports')
def collect_delivery_reports():
    """Every 30 minutes: delivery results NextSMS did not send to the callback."""
    from .sms import collect_nextsms_reports
    return {'updated': collect_nextsms_reports()}


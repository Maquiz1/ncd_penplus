"""Celery application. Schedules are stored in the database (django-celery-beat) and edited in Django admin."""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('ncd_penplus')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

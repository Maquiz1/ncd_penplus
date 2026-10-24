from celery import shared_task

from .detectors import run_all


@shared_task(name='issues.detect_data_issues')
def detect_data_issues():
    """Run the data-quality checks; new problems appear in Data issues."""
    return run_all()

"""Reports as PDF (WeasyPrint): the same body template as the screen, in a print layout."""
import csv

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from audit.models import AuditEvent
from audit.services import log_event
from facilities.scope import scope_description

from .filters import ReportFilters


def pdf_response(request, body_template, context, title):
    from weasyprint import HTML

    html = render_to_string('reporting/pdf_base.html', {
        **context, 'body_template': body_template, 'title': title, 'generated': timezone.localtime(),
        'scope': scope_description(request.user)}, request=request)
    pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = (f'attachment; filename="{slugify(title)}-{timezone.localdate():%Y%m%d}.pdf"')
    return response


class FilteredReportMixin:
    """A report with the shared filters, downloadable as PDF (and CSV when `csv_rows` is defined)."""

    report_title = ''
    body_template = ''
    default_period = 'this_year'
    with_disease = True

    def get_filters(self):
        if not hasattr(self, '_filters'):
            self._filters = ReportFilters(self.request, self.default_period, self.with_disease)
        return self._filters

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters=self.get_filters(), body_template=self.body_template,
                                        report_title=self.report_title, csv_available=hasattr(self, 'csv_rows'),
                                        generated=timezone.localtime(), **kwargs)

    def get(self, request, *args, **kwargs):
        fmt = request.GET.get('format')
        if fmt not in ('pdf', 'csv'):
            return super().get(request, *args, **kwargs)
        context = self.get_context_data(**kwargs)
        log_event(request=request, action=AuditEvent.Action.EXPORT, model='reporting.Report',
                  object_repr=f'{self.report_title} ({fmt.upper()})',
                  changes={'filters': self.get_filters().description})
        if fmt == 'pdf':
            return pdf_response(request, self.body_template, context, self.report_title)
        if not hasattr(self, 'csv_rows'):
            return super().get(request, *args, **kwargs)
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{slugify(self.report_title)}-{timezone.localdate():%Y%m%d}.csv"'
        response.write('﻿')
        writer = csv.writer(response)
        for row in self.csv_rows(context):
            writer.writerow(row)
        return response

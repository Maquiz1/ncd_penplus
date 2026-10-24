from django.core.management.base import BaseCommand
from django.db import transaction

from clinical.models import Diagnosis, Disease
from diseases.models import Condition, match_condition
from issues.detectors import raise_issue
from issues.models import DataIssue

# code, name, active, order
DISEASES = [
    ('diabetes', 'Diabetes', True, 1), ('cardiac', 'Cardiac disease', True, 2), ('scd', 'Sickle cell disease', True, 3),
    ('respiratory', 'Chronic respiratory disease', False, 4), ('kidney', 'Chronic kidney disease', False, 5),
    ('liver', 'Chronic liver disease', False, 6), ('epilepsy', 'Epilepsy', False, 7),
    ('other-ncd', 'Other NCD (respiratory, liver, kidney, other)', False, 9),
]
# disease, code, name, PEN-Plus row, keywords, default, sub-types [(name, keywords)]. Cardiac follows the EDCS
# main-diagnosis form exactly (each diagnosis with its sub-types); a patient can have several.
CONDITIONS = [
    ('diabetes', 't1d', 'Type 1 diabetes', '1.1', 'type 1\nt1d\nt1dm', False, []),
    ('diabetes', 't2d', 'Type 2 diabetes', '1.2', 'type 2\nt2d\nt2dm', False, []),
    ('diabetes', 'dm-other', 'Unspecified / gestational diabetes', '1.3', 'gestational\nnot yet specified\nunspecified', True, []),
    ('cardiac', 'cardiomyopathy', 'Cardiomyopathy', '', 'cardiomyopathy', False,
     ['Ischemic', 'Alcohol-related', 'Peripartum', 'Arrhythmia-related', 'HIV-related', 'Chemotherapy-related',
      'Viral/idiopathic', 'Other']),
    ('cardiac', 'rhd', 'Rheumatic heart disease', '1.4', 'rheumatic\nrhd', False,
     ['Pure mitral stenosis', 'Pure mitral regurgitation', 'Mixed mitral valve disease (MS + MR)',
      'Isolated aortic valve disease (AVD)', 'Mixed mitral and aortic valve disease (MMAVD)', 'Other']),
    ('cardiac', 'chd', 'Congenital heart disease', '1.5', 'congenital', False,
     ['ASD', 'VSD', 'PDA', 'Coarctation of aorta', 'Tetralogy of Fallot', 'Other']),
    ('cardiac', 'htn', 'Severe / uncontrolled hypertension', '1.8', 'uncontrolled hypertension\nsevere hypertension', False, []),
    ('cardiac', 'hhd', 'Hypertensive heart disease', '', 'hypertensive heart', False, []),
    ('cardiac', 'cad', 'Coronary artery disease', '', 'coronary', False, []),
    ('cardiac', 'hf', 'Right heart failure', '1.6', 'right heart failure\nheart failure', False, ['Chronic lung disease']),
    ('cardiac', 'pericardial', 'Pericardial disease', '', 'pericardial', False, ['Tuberculosis', 'HIV', 'Malignancy', 'Other']),
    ('cardiac', 'arrhythmia', 'Arrhythmia', '', 'arrhythmia\natrial fibrillation', False, ['Atrial fibrillation', 'Other']),
    ('cardiac', 'stroke', 'Stroke', '', 'stroke', False, ['Ischemic', 'Hemorrhagic', 'Unknown']),
    ('cardiac', 'thromboembolic', 'Thromboembolic disease', '', 'thromboembolic\nembolism\ndvt', False,
     ['Pulmonary embolism', 'DVT', 'Other']),
    ('cardiac', 'cardiac-other', 'Other cardiac diagnosis', '1.7', 'other cardiac', False, []),
    ('cardiac', 'cardiac-unspecified', 'Cardiac, diagnosis not yet recorded', '', 'category recorded in edcs', True, []),
    ('scd', 'sca', 'Sickle cell anaemia', '1.9', 'sickle', True, []),
    ('scd', 'thalassaemia', 'Thalassaemia', '1.13', 'thalass', False, []),
    ('scd', 'scd-other', 'Other haemoglobinopathy', '1.14', 'other haemoglobinopathy\nhaemoglobinopathy\nhemoglobinopathy', False, []),
    ('respiratory', 'resp', 'Chronic respiratory disease', '1.10', 'asthma\ncopd\nrespiratory', True, []),
    ('kidney', 'ckd', 'Chronic kidney disease', '1.11', 'kidney\nrenal\nckd', True, []),
    ('liver', 'cld', 'Chronic liver disease', '1.12', 'liver\ncirrhosis\nhepatitis', True, []),
    ('epilepsy', 'epilepsy', 'Epilepsy', '1.14', 'epilep\nseizure', True, []),
    ('other-ncd', 'other-ncd', 'Other severe NCD', '1.14', '', True, []),
]


def sub_code(parent_code, name):
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', name.casefold()).strip('-')
    return f'{parent_code}-' + slug[:39 - len(parent_code)].strip('-')


class Command(BaseCommand):
    help = 'Create the PEN-Plus diseases and reporting conditions, and link existing diagnoses to conditions.'

    @transaction.atomic
    def handle(self, *args, **options):
        diseases = {}
        for code, name, active, order in DISEASES:
            disease, created = Disease.all_objects.get_or_create(code=code, defaults={'name': name, 'is_active': active,
                                                                                      'order': order})
            if not created and disease.order != order:
                disease.order = order
                disease.save(update_fields=['order'])
            diseases[code] = disease
        for order, (disease_code, code, name, row, keywords, default, subtypes) in enumerate(CONDITIONS, start=1):
            main, _ = Condition.objects.get_or_create(code=code, defaults={
                'disease': diseases[disease_code], 'name': name, 'indicator_code': row, 'keywords': keywords,
                'is_default': default, 'order': order})
            for sub_order, sub in enumerate(subtypes, start=1):
                keyword = 'other' if sub == 'Other' else sub.casefold()
                Condition.objects.get_or_create(code=sub_code(code, sub), defaults={
                    'disease': main.disease, 'parent': main, 'name': sub, 'keywords': keyword,
                    'order': 90 if sub == 'Other' else sub_order})
        conditions = list(Condition.objects.filter(is_active=True).select_related('parent'))
        linked = defaulted = 0
        for diagnosis in Diagnosis.objects.filter(condition__isnull=True).select_related('disease'):
            condition, _matched = match_condition(diagnosis.description, diagnosis.disease, conditions)
            if condition is not None:
                Diagnosis.objects.filter(pk=diagnosis.pk).update(condition=condition)
                linked += 1
        # Diagnoses that only got their disease's default condition are listed for staff to confirm.
        for diagnosis in (Diagnosis.objects.filter(condition__is_default=True, condition__parent__isnull=True)
                          .select_related('disease', 'patient', 'condition')):
            if diagnosis.disease.conditions.filter(parent__isnull=True).count() < 2:
                continue
            _, new = raise_issue(f'diagnosis-condition:{diagnosis.pk}', 'diagnosis_condition_to_confirm',
                                 DataIssue.Category.CLINICAL, f'Confirm the condition for "{diagnosis.description}"',
                                 f'No specific diagnosis was recorded, so it is counted as "{diagnosis.condition.name}". '
                                 f'Open the patient, edit the diagnosis and choose the correct condition.',
                                 patient=diagnosis.patient, obj=diagnosis.patient, related_date=diagnosis.diagnosis_date)
            defaulted += new
        self.stdout.write(self.style.SUCCESS(
            f'Diseases ready ({Disease.objects.filter(is_active=True).count()} active). Diagnoses linked: {linked} '
            f'({defaulted} on a default condition, listed in Data issues to confirm).'))

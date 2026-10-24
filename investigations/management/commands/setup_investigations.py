from django.core.management.base import BaseCommand
from django.db import transaction

from investigations.models import InvestigationField, InvestigationOrder, InvestigationType
from laboratory.models import LabTest
from pharmacy.models import ConsumableUse, Medication

SEVERITY = 'None\n!Mild\n!Moderate\n!Severe'
NO_YES = 'No\n!Yes'
# (code, name, modality, preparation, [(key, label, type, unit, low, high, choices)])
INVESTIGATION_TYPES = [
    ('ecg-12-lead', '12-lead ECG', 'ecg', '', [
        ('rhythm', 'Rhythm', 'choice', '', None, None, 'Sinus rhythm\n!Atrial fibrillation\n!Atrial flutter\n!Other arrhythmia'),
        ('heart_rate', 'Heart rate', 'number', 'bpm', 60, 100, ''),
        ('lvh', 'Left ventricular hypertrophy', 'choice', '', None, None, NO_YES),
        ('conduction', 'Conduction', 'choice', '', None, None, 'Normal\n!Bundle branch block\n!AV block\n!Other'),
        ('st_t', 'ST / T waves', 'choice', '', None, None, 'Normal\n!Ischaemic changes\n!Other changes'),
        ('interpretation', 'Overall', 'choice', '', None, None, 'Normal\n!Abnormal'),
    ]),
    ('echo', 'Echocardiography', 'echo', '', [
        ('lvef', 'LV ejection fraction', 'number', '%', 50, None, ''),
        ('lv_function', 'LV systolic function', 'choice', '', None, None,
         'Normal\n!Mildly reduced\n!Moderately reduced\n!Severely reduced'),
        ('lv_dilated', 'LV dilated', 'choice', '', None, None, NO_YES),
        ('mitral_stenosis', 'Mitral stenosis', 'choice', '', None, None, SEVERITY),
        ('mitral_regurgitation', 'Mitral regurgitation', 'choice', '', None, None, SEVERITY),
        ('aortic_stenosis', 'Aortic stenosis', 'choice', '', None, None, SEVERITY),
        ('aortic_regurgitation', 'Aortic regurgitation', 'choice', '', None, None, SEVERITY),
        ('rhd_whf', 'RHD (WHF criteria)', 'choice', '', None, None, 'No RHD\n!Borderline RHD\n!Definite RHD'),
        ('congenital', 'Congenital defect', 'choice', '', None, None,
         'None\n!ASD\n!VSD\n!PDA\n!Tetralogy of Fallot\n!Other'),
        ('pericardial_effusion', 'Pericardial effusion', 'choice', '', None, None, 'None\n!Small\n!Moderate\n!Large'),
        ('pulmonary_hypertension', 'Pulmonary hypertension', 'choice', '', None, None, 'Unlikely\n!Possible\n!Likely'),
        ('thrombus', 'Intracardiac thrombus', 'choice', '', None, None, NO_YES),
        ('ivc', 'IVC', 'choice', '', None, None, 'Normal\n!Dilated / non-collapsing'),
    ]),
    ('chest-xray', 'Chest X-ray', 'xray', '', [
        ('cardiomegaly', 'Cardiomegaly', 'choice', '', None, None, NO_YES),
        ('pulmonary_oedema', 'Pulmonary oedema / congestion', 'choice', '', None, None, NO_YES),
        ('pleural_effusion', 'Pleural effusion', 'choice', '', None, None, NO_YES),
        ('other', 'Other findings', 'text', '', None, None, ''),
    ]),
    ('abdominal-us', 'Abdominal ultrasound', 'ultrasound', 'Fasting 6 hours if possible.', [
        ('liver', 'Liver', 'choice', '', None, None, 'Normal\n!Enlarged\n!Other abnormality'),
        ('spleen', 'Spleen', 'choice', '', None, None, 'Normal\n!Enlarged\n!Not seen (auto-splenectomy)'),
        ('kidneys', 'Kidneys', 'choice', '', None, None, 'Normal\n!Abnormal'),
        ('ascites', 'Ascites', 'choice', '', None, None, NO_YES),
    ]),
    ('tcd', 'Transcranial doppler (TCD)', 'tcd', 'Child awake and calm; not within 24 h of a transfusion.', [
        ('tamv', 'Highest TAMV', 'number', 'cm/s', None, 169, ''),
        ('stroke_risk', 'Stroke risk (STOP criteria)', 'choice', '', None, None,
         'Normal (<170)\n!Conditional (170–199)\n!Abnormal (≥200)\nInadequate study'),
    ]),
    ('ct-head', 'CT scan – head', 'ct', '', [
        ('stroke', 'Stroke', 'choice', '', None, None, 'No\n!Ischaemic\n!Haemorrhagic'),
        ('other', 'Other findings', 'text', '', None, None, ''),
    ]),
    ('fundoscopy', 'Fundoscopy / retinal exam', 'other', 'Dilating drops if available.', [
        ('retinopathy', 'Diabetic retinopathy', 'choice', '', None, None,
         'None\n!Background\n!Pre-proliferative\n!Proliferative\n!Maculopathy'),
    ]),
    ('other-procedure', 'Other procedure', 'other', '', []),
]
# Imported EDCS reports (and any created before the catalogue) are linked to these by modality.
DEFAULT_BY_MODALITY = {'ecg': 'ecg-12-lead', 'echo': 'echo', 'xray': 'chest-xray', 'ultrasound': 'abdominal-us',
                       'tcd': 'tcd', 'ct': 'ct-head', 'other': 'other-procedure'}
# Stock items used by tests (code, name, type, unit, reorder level)
SUPPLIES = [
    ('ecg-electrodes', 'ECG electrodes', 'imaging_supply', 'piece', 200),
    ('ecg-paper', 'ECG paper (12-lead sheet)', 'imaging_supply', 'sheet', 100),
    ('ultrasound-gel', 'Ultrasound gel', 'imaging_supply', 'mL', 1000),
    ('hba1c-cartridge', 'HbA1c test cartridge', 'lab_supply', 'test', 20),
    ('sickle-scan', 'Sickle SCAN test kit', 'lab_supply', 'test', 20),
    ('lancets', 'Lancets', 'medical_supply', 'piece', 200),
    ('glucose-strips', 'Glucometer test strips', 'medical_supply', 'strip', 200),
    ('insulin-syringe', 'Insulin syringe', 'medical_supply', 'piece', 200),
    ('glucometer', 'Glucometer', 'equipment', 'piece', 2),
]
# (stock item, quantity, lab test code or None, investigation type code or None)
CONSUMABLES = [
    ('glucose-strips', 1, 'rbg', None), ('lancets', 1, 'rbg', None),
    ('glucose-strips', 1, 'fbg', None), ('lancets', 1, 'fbg', None),
    ('hba1c-cartridge', 1, 'hba1c', None), ('lancets', 1, 'hba1c', None),
    ('sickle-scan', 1, 'scd-result', None),
    ('ecg-electrodes', 10, None, 'ecg-12-lead'), ('ecg-paper', 1, None, 'ecg-12-lead'),
    ('ultrasound-gel', 20, None, 'echo'), ('ultrasound-gel', 20, None, 'abdominal-us'),
]


class Command(BaseCommand):
    help = 'Create the investigation catalogue, test supplies and consumable rules; link existing reports. Safe to re-run.'

    @transaction.atomic
    def handle(self, *args, **options):
        for order, (code, name, modality, preparation, fields) in enumerate(INVESTIGATION_TYPES, start=1):
            itype, created = InvestigationType.objects.get_or_create(code=code, defaults={
                'name': name, 'modality': modality, 'preparation': preparation, 'order': order})
            for forder, (key, label, ftype, unit, low, high, choices) in enumerate(fields, start=1):
                InvestigationField.objects.get_or_create(investigation_type=itype, key=key, defaults={
                    'label': label, 'field_type': ftype, 'unit': unit, 'reference_low': low, 'reference_high': high,
                    'choices': choices, 'order': forder})
        linked = 0
        for modality, code in DEFAULT_BY_MODALITY.items():
            linked += InvestigationOrder.objects.filter(investigation_type__isnull=True, modality=modality).update(
                investigation_type=InvestigationType.objects.get(code=code))
        for code, name, item_type, unit, reorder in SUPPLIES:
            item, created = Medication.objects.get_or_create(code=code, defaults={
                'name': name, 'item_type': item_type, 'dosage_form': 'other', 'dispensing_unit': unit,
                'reorder_level': reorder})
            if not created and item.item_type == Medication.ItemType.MEDICINE:
                item.item_type = item_type
                item.save(update_fields=['item_type'])
        rules = 0
        for item_code, quantity, test_code, type_code in CONSUMABLES:
            target = ({'lab_test': LabTest.objects.filter(code=test_code).first()} if test_code else
                      {'investigation_type': InvestigationType.objects.get(code=type_code)})
            if None in target.values():
                continue
            _rule, created = ConsumableUse.objects.get_or_create(item=Medication.objects.get(code=item_code),
                                                                 defaults={'quantity': quantity}, **target)
            rules += created
        self.stdout.write(self.style.SUCCESS(
            f'Investigation catalogue: {InvestigationType.objects.count()} types; {linked} existing reports linked; '
            f'{rules} new consumable rules.'))

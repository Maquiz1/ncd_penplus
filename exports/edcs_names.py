"""Old EDCS table and column names for the export variables, taken from the importer mappings (integrations/edcs.py).

Exports can use these names instead of the new ones so analysis scripts written for the EDCS data keep working, and
the data dictionary shows them next to each variable. A variable with no EDCS equivalent keeps its new name.
The migrated EDCS forms (form-edcs-*) already use the EDCS column names; only their table is added here.
"""
import functools
import re

# Every dataset: the subject and visit links as EDCS named them.
COMMON = {'old_study_id': 'study_id', 'old_id': 'patient_id', 'patient_id': 'ncd_number', 'sex': 'gender',
          'age_years': 'age', 'encounter_id': 'vid', 'facility': 'site_id'}

# dataset key -> (EDCS table, {new column: EDCS column})
DATASETS = {
    'patients': ('clients', {
        'old_id': 'id', 'first_name': 'firstname', 'middle_name': 'middlename', 'last_name': 'lastname',
        'date_of_birth': 'dob', 'phone': 'phone_number', 'alternate_phone': 'phone_number2',
        'address': 'physical_address', 'residence_district': 'district', 'residence_ward': 'ward',
        'next_of_kin_name': 'guardian_name', 'next_of_kin_phone': 'guardian_phone',
        'registration_facility': 'site_id', 'registration_date': 'clinic_date'}),
    'prescreenings': ('screening', {'date': 'screening_date', 'consent': 'consent', 'notes': 'remarks'}),
    'screenings': ('screening', {'screening_date': 'screening_date', 'consent_obtained': 'consent', 'notes': 'remarks'}),
    'encounters': ('visit', {'encounter_date': 'visit_date', 'stage': 'visit_code', 'clinical_notes': 'comments',
                             'plan_notes': 'next_notes'}),
    'appointments': ('visit', {'scheduled_date': 'expected_date', 'stage': 'visit_code', 'status': 'visit_status',
                               'reason_for_change': 'reasons'}),
    'vitals': ('vital', {'height_cm': 'height', 'weight_kg': 'weight', 'muac_cm': 'muac', 'systolic_bp': 'systolic',
                         'diastolic_bp': 'dystolic', 'pulse': 'pr'}),
    'admissions': ('hospitalization_table', {'admitted_at': 'admission_date', 'discharged_at': 'discharge_date',
                                             'reason': 'admission_reason', 'discharge_summary': 'discharge_diagnosis'}),
    'family': ('sickle_cell_status_table', {'sex': 'sex', 'age_years': 'age', 'scd_status': 'sickle_status',
                                            'recorded_on': 'visit_date', 'notes': 'other'}),
    'medication_history': ('medication_treatments', {
        'medication_name': 'medication_name', 'action': 'medication_action', 'dose': 'dose_description',
        'start_date': 'start_date', 'end_date': 'end_date', 'record_date': 'date'}),
    'imaging': ('results', {'performed_at': 'visit_date'}),
    'programme_enrollments': ('main_diagnosis', {}),
    'status_events': ('summary', {}),
    'diagnoses': ('dgns_complctns_comorbdts', {}),
}


@functools.lru_cache
def _lab_columns():
    """Laboratory test columns (named after the test code) -> the EDCS lab_details / symptoms column."""
    from integrations.edcs import LAB_COLUMNS
    result = {}
    for table in ('lab_details', 'symptoms'):
        for edcs_column, code in LAB_COLUMNS[table].items():
            result.setdefault(re.sub(r'[^a-z0-9]+', '_', code.lower()).strip('_'), (table, edcs_column))
    return result


def edcs_name(dataset, column):
    """(EDCS table, EDCS column) for a dataset variable, or None when EDCS had no such variable."""
    name = column.name
    if dataset.key.startswith('form-edcs-'):
        table = dataset.key[len('form-edcs-'):].replace('-', '_')
        return table, COMMON.get(name, name)
    if dataset.key == 'laboratory':
        if name in _lab_columns():
            return _lab_columns()[name]
        return ('lab_details', COMMON[name]) if name in COMMON else None
    table, columns = DATASETS.get(dataset.key, (None, {}))
    if table is None:
        return None
    if name in columns:
        return table, columns[name]
    if name in COMMON:
        return table, COMMON[name]
    return None


def column_names(dataset, columns, names):
    """Header names for the columns: the new names, or with names='edcs' the EDCS ones (kept unique)."""
    if names != 'edcs':
        return [c.name for c in columns]
    result, used = [], set()
    for c in columns:
        found = edcs_name(dataset, c)
        name = found[1] if found and found[1] not in used else c.name
        used.add(name)
        result.append(name)
    return result

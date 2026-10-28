"""The study forms, in the order of the old EDCS visit, for the Forms page (data and data dictionary per form).

Most EDCS forms were migrated field for field and are listed as they were. Their tables named in MOVED were
split into structured records, so those forms are built from the new records instead:
  lab_details (and lab values elsewhere)  -> Laboratory, one row per lab date
  results (ECG / echo)                    -> Radiology / imaging (the sickle cell tests on it are in Laboratory)
  medication_treatments                   -> Medicines, listed with the Treatment plan form it belonged to
  hospitalization_table                   -> Admission episodes, listed with Hospitalization details
  sickle_cell_status_table                -> Family members / siblings
A form can hold several tables (datasets); they share the patient ID, old ID and encounter ID to link them.
"""
from dataclasses import dataclass, field

from . import datasets as ds


@dataclass
class StudyForm:
    key: str
    title: str
    old_tables: str
    dataset_keys: list
    description: str = ''
    datasets: list = field(default_factory=list)


def legacy(table):
    return f'form-edcs-{table.replace("_", "-")}'


FORMS = [
    ('registration', 'Registration', 'clients', ['patients', legacy('clients')],
     'Patient registration, plus the socio-demographic questions of the EDCS registration form.'),
    ('screening', 'Screening', 'screening', [legacy('screening'), 'screenings'], ''),
    ('demographics', 'Demographics', 'demographic', [legacy('demographic')], ''),
    ('vitals', 'Vital signs', 'vital', ['vitals'], 'Height, weight, MUAC, blood pressure and pulse per visit.'),
    ('patient-category', 'Patient category', 'main_diagnosis', [legacy('main_diagnosis'), 'programme_enrollments'], ''),
    ('history', 'Patient & family history', 'history', [legacy('history')], ''),
    ('symptoms', 'Symptoms & examination', 'symptoms', [legacy('symptoms')], ''),
    ('diagnosis-cardiac', 'Main diagnosis – cardiac', 'cardiac', [legacy('cardiac')], ''),
    ('diagnosis-diabetes', 'Main diagnosis – diabetes', 'diabetic', [legacy('diabetic')], ''),
    ('diagnosis-scd', 'Main diagnosis – sickle cell', 'sickle_cell', [legacy('sickle_cell')], ''),
    ('laboratory', 'Laboratory', 'lab_details', ['laboratory', 'labs'],
     'All lab results: one row per lab date with a column per test, and the same results one row per test '
     '(with units, reference ranges and abnormal flags).'),
    ('imaging', 'Radiology / imaging', 'results', ['imaging'],
     'ECG, echocardiography, X-ray, ultrasound and other examinations.'),
    ('complications', 'Diagnosis, complications & comorbidities', 'dgns_complctns_comorbdts',
     [legacy('dgns_complctns_comorbdts'), 'diagnoses'], ''),
    ('risks', 'Risks', 'risks', [legacy('risks')], ''),
    ('hospitalization', 'Hospitalization / admission', 'hospitalization, hospitalization_details, hospitalization_table',
     [legacy('hospitalization'), legacy('hospitalization_details'), 'admissions'],
     'The hospitalization questions of each visit, and one row per admission episode.'),
    ('treatment', 'Treatment plan & medicines', 'treatment_plan, medication_treatments',
     [legacy('treatment_plan'), 'medication_history', 'prescriptions'],
     'The treatment plan of each visit, and one row per medicine (start, continue, stop) and per prescription.'),
    ('family', 'Sickle cell family status', 'sickle_cell_status_table', ['family'], 'One row per sibling.'),
    ('socio-economic', 'Socio-economic status', 'social_economic', [legacy('social_economic')], ''),
    ('summary', 'Visit summary & outcome', 'summary', [legacy('summary'), 'status_events'], ''),
]


def study_forms():
    """The forms with their datasets (forms whose tables do not exist are left out)."""
    by_key = {d.key: d for d in ds.all_datasets()}
    result = []
    for key, title, tables, keys, description in FORMS:
        found = [by_key[k] for k in keys if k in by_key]
        if found:
            result.append(StudyForm(key, title, tables, keys, description, found))
    listed = {d.key for f in result for d in f.datasets}
    # Forms built in this system (not from EDCS), each as its own form.
    for d in by_key.values():
        if d.group == 'CRF forms' and d.key not in listed:
            result.append(StudyForm(d.key, d.label, '', [d.key], 'Form built in this system.', [d]))
    return result


def get_form(key):
    return next((f for f in study_forms() if f.key == key), None)


def form_datasets(key):
    """Datasets of one form, or of every form for key '*' (the whole study in one workbook)."""
    if key == '*':
        seen, result = set(), []
        for form in study_forms():
            for d in form.datasets:
                if d.key not in seen:
                    seen.add(d.key)
                    result.append(d)
        return result
    form = get_form(key)
    return form.datasets if form else [None]

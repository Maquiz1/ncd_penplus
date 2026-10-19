"""Role (permission group) definitions from the SRS user classes.

Identity access (names, phones, date of birth, address, next of kin, identifiers)
is limited to roles that enter data or must confirm who the patient is: registration,
nursing, clinicians, data clerks, data managers, laboratory, imaging and pharmacy.
Coordinators, investigators, monitors, report viewers and administrators work with
patient IDs only.

Each entry lists permission patterns: "app.codename", "app.*" (all in app) or
"app.view_*" style prefixes. Geographic/facility scope is assigned separately
through StaffAssignment, never through the role.
"""

VIEW_CLINICAL = ['patients.view_patient', 'clinical.view_*', 'encounters.view_*', 'laboratory.view_*',
                 'crf.view_formsubmission', 'crf.view_formdefinition', 'crf.view_formversion', 'research.view_study',
                 'facilities.view_facility']
IDENTITY = ['patients.view_patient_identity']
REPORTS = ['reporting.view_reports']

ROLES = {
    'System Administrator': ['users.*', 'auth.*', 'facilities.*', 'clinical.view_disease', 'clinical.add_disease',
                             'clinical.change_disease', 'clinical.view_clinicalprogramme',
                             'clinical.add_clinicalprogramme', 'clinical.change_clinicalprogramme',
                             'laboratory.view_labtest', 'laboratory.add_labtest', 'laboratory.change_labtest',
                             'pharmacy.view_medication', 'pharmacy.add_medication', 'pharmacy.change_medication',
                             'audit.view_auditevent', 'reporting.view_reportrun'],
    'Facility Administrator': ['users.view_user', 'users.add_user', 'users.change_user', 'facilities.view_facility',
                               'facilities.view_staffassignment', 'facilities.add_staffassignment',
                               'facilities.change_staffassignment', 'reporting.view_reports'],
    'Registration Officer': ['patients.view_patient', 'patients.add_patient', 'patients.change_patient',
                             'clinical.view_screeningsession', 'clinical.view_screening', 'clinical.add_screening',
                             'patients.view_patientidentifier', 'patients.add_patientidentifier',
                             'encounters.view_appointment', 'encounters.add_appointment',
                             'encounters.change_appointment'] + IDENTITY,
    'Nurse / Triage Officer': VIEW_CLINICAL + IDENTITY + [
        'clinical.add_prescreening', 'clinical.add_screening', 'clinical.change_screening',
        'clinical.add_screeningsession', 'clinical.change_screeningsession', 'patients.add_patient',
        'encounters.add_encounter', 'encounters.change_encounter', 'encounters.add_vitals',
        'encounters.add_appointment', 'encounters.change_appointment', 'crf.add_formsubmission',
        'crf.change_formsubmission', 'laboratory.add_laborder'],
    'Clinician': VIEW_CLINICAL + IDENTITY + REPORTS + [
        'clinical.add_*', 'clinical.change_screening', 'clinical.change_diagnosis', 'clinical.change_screeningsession',
        'clinical.change_programmeenrollment', 'clinical.change_prescreening',
        'encounters.add_*', 'encounters.change_*', 'laboratory.add_laborder', 'laboratory.add_investigationorder',
        'laboratory.review_laborder', 'crf.add_formsubmission', 'crf.change_formsubmission',
        'patients.add_patienttransfer', 'patients.view_patienttransfer', 'patients.change_patienttransfer',
        'patients.record_death', 'patients.add_patient', 'research.view_*', 'pharmacy.view_medication', 'pharmacy.view_stockbatch',
        'pharmacy.view_prescription', 'pharmacy.add_prescription', 'pharmacy.change_prescription'],
    'Laboratory Staff': ['patients.view_patient', 'laboratory.view_laborder', 'laboratory.change_laborder',
                         'laboratory.view_labtest', 'laboratory.review_laborder'] + IDENTITY,
    'Pharmacist / Dispenser': ['patients.view_patient', 'pharmacy.*', 'facilities.view_facility',
                               'reporting.view_reports'] + IDENTITY,
    'Imaging / ECG / Echo Staff': ['patients.view_patient', 'laboratory.view_investigationorder',
                                   'laboratory.change_investigationorder'] + IDENTITY,
    'Data Clerk': VIEW_CLINICAL + IDENTITY + [
        'patients.add_patient', 'patients.change_patient', 'patients.add_patientidentifier',
        'clinical.add_screening', 'clinical.change_screening', 'clinical.add_prescreening',
        'clinical.add_screeningsession', 'clinical.change_screeningsession',
        'encounters.add_encounter', 'encounters.change_encounter', 'encounters.add_vitals',
        'encounters.add_appointment', 'encounters.change_appointment',
        'crf.add_formsubmission', 'crf.change_formsubmission'],
    'Data Manager': VIEW_CLINICAL + IDENTITY + REPORTS + [
        'patients.*', 'clinical.change_*', 'encounters.change_*', 'crf.*', 'research.view_*',
        'reporting.export_data', 'reporting.receive_monthly_report', 'audit.view_auditevent', 'pharmacy.view_*',
        'laboratory.view_labtest', 'laboratory.add_labtest', 'laboratory.change_labtest'],
    'Facility Coordinator': VIEW_CLINICAL + REPORTS + [
        'patients.view_patienttransfer', 'patients.add_patienttransfer', 'patients.change_patienttransfer',
        'encounters.add_followupplan', 'encounters.change_followupplan', 'encounters.change_appointment',
        'reporting.receive_monthly_report'],
    'Geographic Coordinator': VIEW_CLINICAL + REPORTS + ['patients.view_patienttransfer',
                                                         'reporting.receive_monthly_report'],
    'Research Coordinator': VIEW_CLINICAL + REPORTS + [
        'research.*', 'encounters.add_followupplan', 'encounters.change_followupplan', 'encounters.add_appointment',
        'encounters.change_appointment', 'encounters.add_encounter', 'encounters.change_encounter',
        'crf.add_formsubmission', 'crf.change_formsubmission', 'reporting.receive_monthly_report'],
    'PI / Co-Investigator': VIEW_CLINICAL + REPORTS + ['research.view_*', 'research.change_study',
                                                       'crf.review_formsubmission', 'reporting.export_data',
                                                       'reporting.receive_monthly_report'],
    'Monitor / Auditor': VIEW_CLINICAL + ['research.view_*', 'audit.view_auditevent', 'patients.view_patienttransfer',
                                          'pharmacy.view_*'],
    'Report Viewer': REPORTS + ['facilities.view_facility'],
}

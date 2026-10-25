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

VIEW_CLINICAL = ['patients.view_patient', 'clinical.view_*', 'encounters.view_*', 'laboratory.view_*', 'investigations.view_*',
                 'crf.view_formsubmission', 'crf.view_formdefinition', 'crf.view_formversion', 'research.view_study',
                 'facilities.view_facility']
IDENTITY = ['patients.view_patient_identity']
SMS_CONSENT = ['patients.record_sms_consent']
REPORTS = ['reporting.view_reports']
# Correcting and soft-deleting clinical records (Records by form). Restoring is for superusers only.
RECORD_CORRECTION = [f'{model.split(".")[0]}.{action}_{model.split(".")[1]}' for action in ('change', 'delete')
                     for model in ('clinical.prescreening', 'clinical.screening', 'clinical.diagnosis',
                                   'clinical.familymember', 'encounters.appointment', 'encounters.encounter',
                                   'encounters.vitals', 'encounters.admission', 'laboratory.laborder',
                                   'investigations.investigationorder', 'pharmacy.prescription',
                                   'pharmacy.medicationrecord', 'crf.formsubmission')]
# Recording that a due monitoring test (HbA1c) was not done, with the reason.
MONITORING = ['laboratory.add_monitoringgap']
STOCK_VIEW = ['pharmacy.view_medication', 'pharmacy.view_stockbatch', 'pharmacy.view_stockshortfall', 'pharmacy.view_consumableuse']


def _changes(*models):
    return [f'{model.split(".")[0]}.{action}_{model.split(".")[1]}' for model in models
            for action in ('add', 'change', 'delete')]


# System setup shared by every site: only staff with a National assignment (or superusers) may change it,
# whatever their role. Everyone keeps the matching view permissions.
NATIONAL_ONLY = _changes(
    'clinical.disease', 'clinical.clinicalprogramme', 'diseases.condition', 'laboratory.labtest', 'laboratory.labpanel',
    'laboratory.labcategory', 'investigations.investigationtype', 'investigations.investigationfield',
    'pharmacy.consumableuse', 'crf.formdefinition', 'crf.formversion', 'crf.formsection', 'crf.formfield',
    'pharmacy.medication', 'facilities.facility', 'facilities.zone', 'facilities.region', 'facilities.district',
    'facilities.ward', 'facilities.street', 'notifications.siteconfiguration', 'users.position',
) + ['clinical.restore_disease', 'crf.publish_formversion', 'facilities.merge_locations']

ROLES = {
    'System Administrator': ['users.*', 'auth.*', 'facilities.*', 'clinical.view_disease', 'clinical.add_disease',
                             'clinical.change_disease', 'clinical.delete_disease', 'diseases.*', 'clinical.view_clinicalprogramme',
                             'clinical.add_clinicalprogramme', 'clinical.change_clinicalprogramme',
                             'laboratory.view_labtest', 'laboratory.add_labtest', 'laboratory.change_labtest',
                             'pharmacy.view_medication', 'pharmacy.add_medication', 'pharmacy.change_medication',
                             'audit.view_auditevent', 'reporting.view_reportrun',
                             'notifications.*siteconfiguration', 'notifications.view_emaillog',
                             'investigations.*investigationtype', 'investigations.*investigationfield',
                             'pharmacy.*consumableuse'],
    'Facility Administrator': ['users.view_user', 'users.view_position', 'users.add_user', 'users.change_user', 'facilities.view_facility',
                               'facilities.view_staffassignment', 'facilities.add_staffassignment',
                               'facilities.change_staffassignment', 'reporting.view_reports'],
    'Registration Officer': SMS_CONSENT + ['patients.view_patient', 'patients.add_patient', 'patients.change_patient',
                             'clinical.view_screeningsession', 'clinical.view_screening', 'clinical.add_screening',
                             'patients.view_patientidentifier', 'patients.add_patientidentifier',
                             'encounters.view_appointment', 'encounters.add_appointment',
                             'encounters.change_appointment'] + IDENTITY,
    'Nurse / Triage Officer': VIEW_CLINICAL + IDENTITY + SMS_CONSENT + [
        'clinical.add_prescreening', 'clinical.add_screening', 'clinical.change_screening', 'clinical.add_familymember',
        'clinical.change_familymember',
        'clinical.add_screeningsession', 'clinical.change_screeningsession', 'patients.add_patient',
        'encounters.add_encounter', 'encounters.change_encounter', 'encounters.add_vitals',
        'encounters.add_appointment', 'encounters.change_appointment', 'crf.add_formsubmission',
        'crf.change_formsubmission', 'laboratory.add_laborder', 'issues.view_dataissue', 'issues.change_dataissue'] + MONITORING,
    'Clinician': VIEW_CLINICAL + IDENTITY + REPORTS + SMS_CONSENT + [
        'clinical.add_prescreening', 'clinical.add_screening', 'clinical.add_diagnosis', 'clinical.add_programmeenrollment',
        'clinical.add_screeningsession', 'clinical.change_screening', 'clinical.change_diagnosis',
        'clinical.change_screeningsession',
        'clinical.change_programmeenrollment', 'clinical.change_prescreening',
        'encounters.add_*', 'encounters.change_*', 'laboratory.add_laborder', 'investigations.add_investigationorder',
        'laboratory.review_laborder', 'investigations.review_investigationorder', 'clinical.add_familymember',
        'clinical.change_familymember', 'crf.add_formsubmission', 'crf.change_formsubmission',
        'patients.add_patienttransfer', 'patients.view_patienttransfer', 'patients.change_patienttransfer',
        'patients.record_death', 'patients.add_patient', 'research.view_*', 'pharmacy.view_medication', 'pharmacy.view_stockbatch',
        'pharmacy.view_prescription', 'pharmacy.add_prescription', 'pharmacy.change_prescription', 'issues.view_dataissue', 'issues.change_dataissue'] + MONITORING,
    'Laboratory Staff': ['patients.view_patient', 'laboratory.view_laborder', 'laboratory.change_laborder',
                         'laboratory.view_labtest', 'laboratory.review_laborder', 'issues.view_dataissue', 'issues.change_dataissue'] + IDENTITY
                        + MONITORING + ['laboratory.view_monitoringgap']
                        + STOCK_VIEW,
    'Pharmacist / Dispenser': ['patients.view_patient', 'pharmacy.view_*', 'pharmacy.add_*', 'pharmacy.change_*',
                               'pharmacy.adjust_stock', 'facilities.view_facility',
                               'reporting.view_reports', 'issues.view_dataissue', 'issues.change_dataissue'] + IDENTITY,
    'Imaging / ECG / Echo Staff': ['patients.view_patient', 'investigations.view_investigationorder',
                                   'investigations.change_investigationorder', 'investigations.view_investigationtype',
                                   'issues.view_dataissue', 'issues.change_dataissue'] + IDENTITY + STOCK_VIEW,
    'Data Clerk': VIEW_CLINICAL + IDENTITY + SMS_CONSENT + [
        'patients.add_patient', 'patients.change_patient', 'patients.add_patientidentifier',
        'clinical.add_screening', 'clinical.change_screening', 'clinical.add_prescreening',
        'clinical.add_screeningsession', 'clinical.change_screeningsession', 'clinical.add_familymember',
        'clinical.change_familymember',
        'encounters.add_encounter', 'encounters.change_encounter', 'encounters.add_vitals',
        'encounters.add_appointment', 'encounters.change_appointment',
        'crf.add_formsubmission', 'crf.change_formsubmission'] + MONITORING,
    'Data Manager': VIEW_CLINICAL + IDENTITY + REPORTS + [
        'patients.*', 'clinical.change_*', 'encounters.change_*', 'crf.*', 'research.view_*',
        'reporting.export_data', 'exports.export_identifiable', 'reporting.receive_monthly_report',
        'audit.view_auditevent', 'pharmacy.view_*',
        'laboratory.view_labtest', 'laboratory.add_labtest', 'laboratory.change_labtest', 'issues.*',
        'encounters.add_clinicsession', 'encounters.change_clinicsession', 'notifications.*smsmessage',
        'notifications.view_emaillog', 'notifications.view_siteconfiguration', 'facilities.merge_locations', 'facilities.view_ward', 'facilities.view_street',
        'clinical.add_disease', 'clinical.change_disease', 'clinical.delete_disease', 'diseases.*',
        'investigations.*investigationtype', 'investigations.*investigationfield', 'pharmacy.*consumableuse',
        'clinical.add_familymember', 'clinical.change_familymember'] + RECORD_CORRECTION + MONITORING,
    'Facility Coordinator': VIEW_CLINICAL + REPORTS + [
        'patients.view_patienttransfer', 'patients.add_patienttransfer', 'patients.change_patienttransfer',
        'encounters.add_followupplan', 'encounters.change_followupplan', 'encounters.change_appointment',
        'reporting.receive_monthly_report', 'encounters.add_clinicsession', 'encounters.change_clinicsession',
        'notifications.view_smsmessage', 'notifications.add_smsmessage'],
    'Geographic Coordinator': VIEW_CLINICAL + REPORTS + ['patients.view_patienttransfer',
                                                         'reporting.receive_monthly_report'],
    'Research Coordinator': VIEW_CLINICAL + REPORTS + [
        'research.*', 'encounters.add_followupplan', 'encounters.change_followupplan', 'encounters.add_appointment',
        'encounters.change_appointment', 'encounters.add_encounter', 'encounters.change_encounter',
        'crf.add_formsubmission', 'crf.change_formsubmission', 'reporting.receive_monthly_report',
        'reporting.export_data'],
    'PI / Co-Investigator': VIEW_CLINICAL + REPORTS + ['research.view_*', 'research.change_study',
                                                       'crf.review_formsubmission', 'reporting.export_data',
                                                       'reporting.receive_monthly_report'],
    'Monitor / Auditor': VIEW_CLINICAL + ['research.view_*', 'audit.view_auditevent', 'patients.view_patienttransfer',
                                          'pharmacy.view_*'],
    'Report Viewer': REPORTS + ['facilities.view_facility'],
}


# Positions (job titles). The level is not part of the name: a Nurse assigned to a facility is a site nurse.
# (code, name, default role, typical level, note)
POSITIONS = [
    ('nurse', 'Nurse', 'Nurse / Triage Officer', 'facility', ''),
    ('medical-doctor', 'Medical Doctor', 'Clinician', 'facility', ''),
    ('clinical-officer', 'Clinical Officer', 'Clinician', 'facility', ''),
    ('specialist', 'Specialist / Consultant', 'Clinician', 'region', 'Cardiologist, paediatrician, physician, mentor.'),
    ('lab-technician', 'Laboratory Technician / Technologist', 'Laboratory Staff', 'facility', ''),
    ('pharmacist', 'Pharmacist', 'Pharmacist / Dispenser', 'facility', ''),
    ('pharmaceutical-technician', 'Pharmaceutical Technician / Dispenser', 'Pharmacist / Dispenser', 'facility', ''),
    ('ecg-echo-technician', 'ECG / Echo Technician', 'Imaging / ECG / Echo Staff', 'facility', ''),
    ('radiographer', 'Radiographer / Sonographer', 'Imaging / ECG / Echo Staff', 'facility', ''),
    ('records-clerk', 'Records / Registration Clerk', 'Registration Officer', 'facility', ''),
    ('data-clerk', 'Data Clerk', 'Data Clerk', 'facility', ''),
    ('data-manager', 'Data Manager', 'Data Manager', 'facility',
     'Manages data at the level of the assignment: facility, district, region, zone or national.'),
    ('facility-in-charge', 'Facility In-charge', 'Facility Administrator', 'facility', ''),
    ('coordinator', 'Coordinator', 'Facility Coordinator', 'facility',
     'At district, region or zone level give the Geographic Coordinator role instead.'),
    ('study-coordinator', 'Study Coordinator', 'Research Coordinator', 'national', ''),
    ('principal-investigator', 'Principal Investigator', 'PI / Co-Investigator', 'national', ''),
    ('co-investigator', 'Co-Investigator', 'PI / Co-Investigator', 'national', ''),
    ('monitor', 'Monitor / Auditor', 'Monitor / Auditor', 'national', ''),
    ('me-officer', 'M&E / Health Officer', 'Report Viewer', 'district', 'District, regional or MoH reporting officer.'),
    ('system-administrator', 'System Administrator', 'System Administrator', 'national', ''),
]
# Position names used in the old EDCS "position" table.
EDCS_POSITIONS = {1: 'data-manager', 2: 'coordinator', 3: 'principal-investigator', 5: 'pharmacist',
                  6: 'medical-doctor', 7: 'nurse', 8: 'lab-technician'}
EDCS_POSITION_NAMES = {'Data Manager': 'data-manager', 'Coordinator': 'coordinator',
                       'Project Principle Investigator': 'principal-investigator', 'Site Pharmarcist': 'pharmacist',
                       'Site Doctors': 'medical-doctor', 'Site Nurse': 'nurse', 'Site Lab Personnel': 'lab-technician'}

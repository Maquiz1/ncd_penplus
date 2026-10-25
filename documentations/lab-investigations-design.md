# Laboratory and investigations — design

Draft, 25 Oct 2026. Based on the *GoTHOMIS Central User Manual* (PO-RALG, Nov 2023), chapters 3.4, 6, 7.6–7.8 and 11, and on how PEN-Plus clinics work.

## 1. How PEN-Plus clinics work

- The **PEN-Plus clinic** is a separate unit inside the hospital, with its own staff, **its own medicines and lab supplies**, and investigations **paid for by the project**.
- Trained hospital staff (clinicians, nurses, **lab personnel, pharmacists, radiologists / echo staff**) have accounts in both GoTHOMIS and NCD PEN-Plus.
- A PEN-Plus request is made **in this system**. The hospital lab or radiology department sees it here, processes the sample or examination, and **enters the result here**.
- Sometimes the same test is also requested in GoTHOMIS for hospital purposes. That is allowed; this system does not depend on it.
- **Reports come only from this system**, because the project cannot query GoTHOMIS. So every PEN-Plus order, result, medicine and visit must be complete here.

This system is therefore the clinic's full record for PEN-Plus patients. The lab workflow follows GoTHOMIS's steps, so staff who use both find it familiar.

## 2. GoTHOMIS steps and their equivalent here

| GoTHOMIS (manual §) | NCD PEN-Plus | Status |
|---------------------|--------------|--------|
| Item setup: item code, sub-department, TAT, samples, result options (3.4.2) | Lab catalogue: test, category, unit, ranges, result type, specimen | **Extend**: sub-department, GoTHOMIS code, TAT, sample types |
| Department access (3.4.1) | Roles plus facility and disease scope | Enough for now (see L4) |
| Order: lab / radiology → sub-department → items, Urgent, comment (7.6) | Lab order with tests or panels, urgent, notes; investigation order | **Extend**: group the picker by sub-department |
| Sample collection: sample type, sample number (11.1) | "Collect" with a free-text specimen ID | **Extend**: sample type, automatic sample number, **received in lab** |
| Results posting (11.2, 11.3) | "Enter results"; investigation report with attachment | **Extend**: attachment on lab results |
| Results approval: approve / return / reject (11.4) | — | **New** (optional per facility, see L2) |
| Results review by clinician (11.5) | "Review" | Keep; clinician **query** back to the lab |
| Results amendment (11.6) | — | **New**: versions with reason |
| Results history (11.7) | Labs tab, HbA1c banner | **Extend**: cumulative table and trends |
| Confirm diagnosis with ICD-10 (7.8) | Diagnosis, condition with ICD-10/11 code; "results available: confirm diagnosis" prompt | Exists; fill ICD-10 codes |
| Triage initial investigations (6) | Vitals (with blood glucose) | **Extend**: point-of-care tests |
| Blood orders | — | Later (L5) |
| Billing (5) | — | Not needed: PEN-Plus pays |

## 3. Design

### 3.1 Catalogue (Administration → Laboratory catalogue, National level)

- **Sub-department** = current `LabCategory`, plus a **department** (Laboratory / Radiology & imaging / Cardiology). Seeded with the GoTHOMIS names: Clinical Chemistry, Haematology, Immunology, Microbiology, Parasitology; X-ray, Ultrasound, CT scan, MRI; ECG, Echocardiography, TCD.
- **Test / investigation**: add these fields:
  - `gothomis_code`, e.g. `LAB00008` Uric acid or `LAB00022` FBC (for the later integration and for staff recognition);
  - `tat_target_hours`;
  - allowed **sample types**;
  - `point_of_care`, for tests done in the clinic itself, such as glucometer, HemoCue or HbA1c POC.
- **Sample types**: Whole blood (EDTA), Serum, Plasma, Capillary blood, Urine, Stool, … with container or tube colour.
- **Supplies per test**: already exists (`consume_for_service`). Kits and reagents come out of **PEN-Plus stock** when a result is entered, so a kit stock-out shows in the stock reports and in the "HbA1c not done: stock-out" reason.
- Investigations: add **MRI**; keep the structured report fields.

### 3.2 Laboratory workflow

```
ordered ─collect─▶ collected ─receive─▶ in lab ─post─▶ resulted ─approve*─▶ approved ─review─▶ reviewed
   │                  │                   │               │                     │
 cancel        reject sample        reject sample   return for           amend (version + reason;
              (re-collect)         (re-collect)     correction            clinician sees "amended")
                                                                       * approval optional per facility
```

| Step | Who | What is recorded |
|------|-----|------------------|
| **Order** | Clinician / nurse in the clinic | Tests or panels picked by sub-department, urgent, clinical notes, encounter |
| **Collect** | Nurse (clinic) or lab | **Sample type**, automatic **sample number** (`<facility code>-YYMMDD-NNN`, printed on the request slip or label), collected by and at |
| **Receive** | Lab | Received at and by. Or **reject sample** (haemolysed, clotted, insufficient, unlabelled, delayed), which sends it back for re-collection |
| **Post result** | Lab | Value, option or text (as now), comment, optional **PDF/image** (analyser printout), and optionally the **GoTHOMIS reference** when the same test was also requested there |
| **Approve** | Lab in-charge | Approve, or **return for correction** with a reason. Can be switched off for a facility where one technician works alone |
| **Review** | Clinician | Acknowledge; or **query** with a comment that goes back to the lab |
| **Amend** | Lab in-charge | New result version; the old value, who, when and the reason are kept; the review resets so the clinician sees the change |

Lab staff work from **worklists named like GoTHOMIS's**:
1. To collect / receive
2. Results posting
3. Results approval
4. Rejected samples
5. Amendments
6. Results history

The worklists show only the user's facilities and diseases. Each one can be searched by Patient ID, old ID, **GoTHOMIS MRN** or sample number, and urgent orders come first.

Clinicians get **Results to review**, and the patient's Labs tab shows a **cumulative table** (tests × dates) and **trend charts** (HbA1c, creatinine, Hb, INR).

### 3.3 Investigations (ECG, echo, X-ray, ultrasound, CT, MRI)

| Step | Who | What |
|------|-----|------|
| Request | Clinician | Type, indication, urgent |
| Schedule (optional) | Radiology / echo staff | Date and time; the patient gets an appointment or SMS reminder |
| Report | Radiographer / echo / ECG staff | Structured fields, findings, conclusion, **image/PDF** |
| Approve (optional) | Radiologist / cardiologist | Approve or return; useful when echoes are done by trained mid-level providers under mentorship |
| Review | Clinician | Acknowledge / query |
| Amend | Reporter or approver | Version with reason |

### 3.4 Pharmacy

Same principle: PEN-Plus medicines are a **separate stock** in this system (already built: stock batches, dispensing, shortfalls). Hospital pharmacists who are trained on PEN-Plus dispense here.

### 3.5 Point-of-care tests in the clinic

Glucose, Hb, urine dipstick and HbA1c POC entered at triage or at the visit: **order, collect and result in one step**, using clinic stock. They appear in lab history, indicators and the HbA1c due list like any other result.

### 3.6 Reports

- **Turnaround time** per test and sub-department: ordered→collected, collected→received, received→resulted and resulted→approved, with % within the target.
- **Workload**: tests per month, site and sub-department; rejected samples by reason.
- **Pending results**: orders with no result after the TAT target, so the clinic can follow up with the lab.
- **Supplies used** per test (from stock), for PPF stock-out reporting.

### 3.7 GoTHOMIS (later)

- Store now: the patient's **GoTHOMIS MRN**, the facility's GoTHOMIS / HFR code, and the test's `gothomis_code`. Optionally store the GoTHOMIS reference on a result.
- Later, with the API specification from PO-RALG:
  - a result posted here can be **sent to GoTHOMIS**, so the hospital record is complete without entering it twice;
  - or GoTHOMIS results can be **pulled** for PEN-Plus patients.

  Which direction is needed depends on whether the hospital wants PEN-Plus results inside GoTHOMIS.
- Nothing in phase 1 depends on GoTHOMIS.

## 4. Decisions needed

| # | Question | Suggestion |
|---|----------|------------|
| L1 | Which results count for indicators and the HbA1c due list: *resulted* or only *approved*? | *Resulted*; flag results not approved after 48 h |
| L2 | Lab approval step: always, optional per facility, or not at all? | Optional per facility, on by default; the approver must be a different person |
| L3 | Can clinicians reject a result, or only query it? | Query only; the lab decides whether to amend |
| L4 | Do lab staff need to be limited to their bench (haematology vs chemistry)? | Not now; PEN-Plus lab staff are few |
| L5 | Blood orders for sickle cell transfusions: now or later? | Later |
| L6 | Sample number format, and is a printed label or slip needed? | `<facility code>-YYMMDD-NNN`; printable request slip with the number |
| L7 | Who collects samples: clinic nurse, or the patient goes to the lab? | Both allowed; "collect" and "receive" can be done by the same person in one step |
| L8 | Should results also go into GoTHOMIS later (push), or should GoTHOMIS results come here (pull)? | Ask the hospitals; request the API specification from PO-RALG now |

## 5. Build order

1. **Catalogue**: sub-departments with departments, sample types, GoTHOMIS codes, TAT targets, POC flag, MRI.
2. **Lab workflow**: sample type and sample number, receive in lab, reject sample, optional approval, clinician query, amendment versions, attachments.
3. **Worklists** by step for lab staff, Results to review for clinicians, results history, cumulative table and trends.
4. **Investigations**: optional approval, query and amendment; scheduling with appointment or SMS.
5. **POC tests** at triage or visit.
6. **Reports**: TAT, workload, pending results, supplies used.
7. **GoTHOMIS integration** when the API specification is available.

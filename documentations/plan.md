# NCD PEN-Plus — plan and open decisions

Last updated: 25 Oct 2026. Tick items as they are done; add the decision and date next to each answered question.

---

## 1. Decisions needed

### Indicators (PEN Plus Indicators NOP_Tz 2024_12_17)

| # | Question | Options | Current behaviour |
|---|----------|---------|-------------------|
| D1 | **Surgical referral:** what counts as "referred for surgery"? | (a) only *awaiting surgery* and *cardiac surgery / intervention*; (b) also *cardiology referral* | (b) |
| D2 | **Food insecurity:** when is a patient food-insecure? | (a) a "yes" to **any** of the three food questions; (b) "yes" to **all three** (old EDCS) | (a) |
| D3 | **Social support:** which denominator? | (a) all active patients; (b) active patients with a treatment plan (social support answered) in the last 3 months | (b) |

### HbA1c monitoring

| # | Question | Current setting |
|---|----------|-----------------|
| D4 | Interval between HbA1c tests | 3 months (Administration → Monitoring settings) |
| D5 | After an overdue HbA1c is recorded as **not done**, when is it due again? | 1 month later (Administration → Monitoring settings) |
| D6 | Reasons for "not done": not ordered at the visit, patient did not attend, sample not collected, kits/reagents out of stock, machine not working, patient refused, could not afford, result lost, other. Add or remove any? | as listed |
| D7 | Should "not done because of stock-out / machine" be counted in the stock-out indicators (PPF)? | not linked yet |

### Data and migration

| # | Question | Recommendation |
|---|----------|----------------|
| D8 | Keep the old **NCD24-xxxx** numbers as an extra identifier (type "Other") so staff can still search by them? | Yes, if staff still use the old numbers on paper files |
| D9 | Old EDCS **stock** balances: migrate or start fresh? | Physical count at go-live, enter as opening balances |
| D10 | Check with sites: NCD24-0204 has `dka_number = 95` (probably a typo); one transport cost entered as "I0000" | Correct in the source form, or in Records |

---

## 2. Actions (no decision needed)

- [ ] Restart Celery with the new settings folder: `celery -A config worker -l info` and `celery -A config beat -l info`
- [ ] Give Data Managers / PI a **National** staff assignment so they see all sites
- [ ] Assign diseases to staff who should see only some diseases (Staff → edit → Diseases)
- [ ] Add a prefix (Dr., Mr., …) and position to staff records
- [ ] Review the HbA1c due list (Monitoring → HbA1c due list): 83 overdue on 25 Oct 2026

---

## 3. Next phase

1. **Export field selection:** choose which fields go into an export (per form or across forms), save field sets, codebook follows the selection.
2. **Visits and encounters:** map encounter status and outcomes (attended, missed, LTFU, transferred, died…) with monthly and quarterly summaries per site.
3. **Catchment wards, Zanzibar and the places level:** catchment areas per facility, Zanzibar regions/districts, and a places level under the street/village.
4. **PPF1–PPF6:** manual quarterly entry (trained mid-level providers by cadre, specialist mentorship days, stock-out days for drugs and equipment) and indicator targets.
5. **Laboratory and investigations (full workflow in this system):** hospital lab, radiology and pharmacy staff trained on PEN-Plus process PEN-Plus requests and enter results here. Sub-departments, sample types and numbers, receive in lab, sample rejection, optional approval, clinician query, amendments, worklists, results history and trends, POC tests, TAT and workload reports. Design and decisions L1–L8: [lab-investigations-design.md](lab-investigations-design.md).
6. **GoTHOMIS integration (later):** send PEN-Plus results to GoTHOMIS or pull GoTHOMIS results; needs the API specification from PO-RALG. Store GoTHOMIS MRN, facility code and test codes from now on.
7. **Monitoring for other tests:** the HbA1c due list is built so other routine tests (e.g. echo for cardiac patients, Hb for sickle cell) can be added the same way. Which tests and intervals?

---

## 4. Go-live (when the team decides)

1. Fresh dump of the live PHP EDCS into `documentations/Database/`, then full rebuild and `import_penplus_edcs` (about 10 min).
2. Email: switch `DJANGO_EMAIL_BACKEND` to SMTP in `.env` (or add an active Site configuration under Administration → Email settings), set `SITE_URL`. Send a test email, then Staff → Send activation links and check the Email log.
3. Restart the app and the Celery worker and beat after changing `.env`.
4. Run the data-issue detectors once (`run_all`) and hand the Data issues list to each site.

"""Open questions for the study team, shown on the Decisions sheet of the data dictionary.

Each one says what is unclear or missing, what the system does for now, and who should decide. The same questions
are discussed in documentations/plan.md (D) and documentations/lab-investigations-design.md (L); IDs are the same.
When an answer comes, record it here with the answer and set status to Answered (and raise the dictionary version).
(id, priority, area, form / item, problem found, what the system does now / proposal, who decides, answer, status)
"""

DECISIONS = [
    ('D0', 'Example', 'Example', 'Treatment plan', 'A choice list has 3 options but the paper form has 4 tick boxes.',
     'Add the missing option.', 'Data manager', 'Add option 4 "Other". Agreed 5 Nov.', 'Example'),

    # Indicators (PEN Plus Indicators NOP_Tz 2024_12_17)
    ('D1', 'High', 'Indicators', 'Surgical referral indicator',
     'The indicator guide does not say what counts as "referred for surgery": only subjects awaiting surgery or '
     'having cardiac surgery / intervention, or also those referred to cardiology.',
     'Counts awaiting surgery, cardiac surgery / intervention and cardiology referral (option b).',
     'PI + M&E officer', '', 'Open'),
    ('D2', 'High', 'Indicators', 'Food insecurity indicator (socio-economic form)',
     'A subject can be food-insecure on a "yes" to any of the three food questions, or only on "yes" to all three '
     '(the old EDCS rule). The two give different numbers.',
     'Food-insecure on a "yes" to any of the three questions (option a).', 'PI + M&E officer',
     'Option a (10 Nov 2026): food-insecure when ANY of the three questions is answered 2 "Sometimes (3-10 times)" '
     'or 3 "Often (>10 times)" on the latest socio-economic form.', 'Answered'),
    ('D3', 'High', 'Indicators', 'Social support indicator',
     'The denominator is not stated: all active subjects, or active subjects whose social support was asked (a '
     'treatment plan in the last 3 months).',
     'Active subjects with a treatment plan (social support answered) in the last 3 months (option b).',
     'PI + M&E officer', '', 'Open'),

    # HbA1c monitoring
    ('D4', 'Medium', 'HbA1c monitoring', 'HbA1c due list',
     'The interval between HbA1c tests is not set by the protocol.',
     '3 months; changeable under Administration → Monitoring settings.', 'PI + clinicians',
     'Every 3 months, as agreed by the study team (8 Nov 2026). The system already uses 3 months.', 'Answered'),
    ('D5', 'Medium', 'HbA1c monitoring', 'HbA1c due list',
     'When an overdue HbA1c is recorded as not done, it is not stated when it is due again.',
     'Due again 1 month later; changeable under Administration → Monitoring settings.', 'PI + clinicians', '', 'Open'),
    ('D6', 'Low', 'HbA1c monitoring', 'HbA1c not done: reason',
     'The list of reasons for "not done" was proposed by the system, not by the study.',
     'Not ordered at the visit, subject did not attend, sample not collected, kits / reagents out of stock, machine '
     'not working, subject refused, could not afford, result lost, other. Add or remove any.',
     'Clinicians + laboratory', '', 'Open'),
    ('D7', 'Low', 'HbA1c monitoring', 'Stock-out indicators (PPF)',
     'An HbA1c not done because of a stock-out or a broken machine is not counted in the stock-out indicators.',
     'Not linked yet. Decide whether these reasons should count.', 'M&E officer + pharmacy', '', 'Open'),

    # Data and migration
    ('D8', 'Medium', 'Migration', 'Subject identifiers',
     'Subjects had NCD24-xxxx numbers in the old system. Staff may still use them on paper files.',
     'Proposed: keep them as an extra identifier (type "Other") so they can be searched, if staff still use them.',
     'Data manager + sites', '', 'Open'),
    ('D9', 'High', 'Migration', 'Pharmacy stock',
     'The old EDCS stock balances may not match what is on the shelves.',
     'Proposed: a physical count at go-live, entered as opening balances (EDCS balances not migrated).',
     'Pharmacy + data manager', '', 'Open'),
    ('D10', 'Low', 'Data quality', 'Lab details (dka_number); socio-economic (transport cost)',
     'NCD24-0204 has dka_number = 95 (probably a typo), and one transport cost was entered as "I0000".',
     'Imported as entered. Correct them in the source form or under Records once the sites confirm.',
     'Site coordinators', '', 'Open'),

    # Laboratory and investigations
    ('L1', 'High', 'Laboratory', 'Indicators and the HbA1c due list',
     'It is not decided whether a result counts once it is resulted, or only once it is approved.',
     'Proposed: resulted counts; results not approved after 48 h are flagged.', 'PI + laboratory', '', 'Open'),
    ('L2', 'Medium', 'Laboratory', 'Result approval',
     'An approval step for lab results can be always on, optional per facility, or not used.',
     'Proposed: optional per facility, on by default; the approver must be a different person.',
     'Laboratory managers', '', 'Open'),
    ('L3', 'Low', 'Laboratory', 'Clinician review of results',
     'It is not decided whether clinicians can reject a result or only query it.',
     'Proposed: query only; the laboratory decides whether to amend.', 'Clinicians + laboratory', '', 'Open'),
    ('L4', 'Low', 'Laboratory', 'Lab staff access',
     'Lab staff could be limited to their bench (haematology, chemistry).',
     'Not limited for now: PEN-Plus lab staff are few.', 'Laboratory managers', '', 'Open'),
    ('L5', 'Low', 'Laboratory', 'Blood orders (sickle cell transfusions)',
     'Blood orders for transfusions are not in the system.', 'Proposed: later.', 'PI', '', 'Open'),
    ('L6', 'Medium', 'Laboratory', 'Sample number and label',
     'The sample number format is not set, nor whether a printed label or slip is needed.',
     'Proposed: <facility code>-YYMMDD-NNN, with a printable request slip showing the number.',
     'Laboratory managers', '', 'Open'),
    ('L7', 'Medium', 'Laboratory', 'Sample collection',
     'It is not decided who collects samples: the clinic nurse, or the subject goes to the laboratory.',
     'Both allowed; "collect" and "receive" can be done by the same person in one step.', 'Sites', '', 'Open'),
    ('L8', 'Medium', 'Laboratory', 'GoTHOMIS',
     'It is not decided whether results go to GoTHOMIS (push) or GoTHOMIS results come here (pull).',
     'Proposed: ask the hospitals; request the API specification from PO-RALG now.', 'PI + hospitals',
     'Neither for now (8 Nov 2026): results are taken from GoTHOMIS and entered here by hand by lab staff trained '
     'on this system. No automatic link.', 'Answered'),
]

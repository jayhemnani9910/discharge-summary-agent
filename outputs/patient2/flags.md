# Flags for clinician review

Total: 41

### CRITICAL
- **[drug_interaction] drug_interaction**: HIGH severity interaction: Additive QT-interval prolongation risk from combining Loperamide + Ofloxacin (in OFLOX TZ) + Ondansetron (EMESET). These three drugs are all on the discharge medication list. Clinician should review and consider ECG monitoring or alternative medications. (pages [2])

### HIGH
- **[unreadable] source_document**: Page 38 could not be transcribed; its content is unknown. (pages [38])
- **[unreadable] source_document**: Page 39 could not be transcribed; its content is unknown. (pages [39])
- **[conflict] patient_demographics**: Sources disagree on Patient Demographics: 'Patient name: NOT DOCUMENTED in readable pages; Age: NOT DOCUMENTED; Sex: CONFLICT (female per page 1, male per page 46); Weight: 71 kg; Ward: HDU/SDICU' (p1); 'Patient name: NOT DOCUMENTED in readable pages; Age: NOT DOCUMENTED; Sex: CONFLICT (male per page 46, female per page 1); Weight: 71 kg' (p46) (pages [1, 46])
- **[conflict] principal_diagnosis**: Sources disagree on Principal Diagnosis: 'Suspected colitis / Grade-I fatty liver (per discharge summary)' (p2); 'DKA (Diabetic Ketoacidosis) - per ER diagnosis and multiple consultation notes' (p3) (pages [2, 3])
- **[conflict] principal_diagnosis**: Sources disagree on Principal Diagnosis: 'Suspected colitis / Grade-I fatty liver (per discharge summary)' (p2); 'DKA (Diabetic Ketoacidosis) - per ER diagnosis and multiple consultation notes' (p3); 'Acute gastroenteritis with dehydration / Urinary tract infection (per admission note)' (p1) (pages [1, 2, 3])
- **[conflict] principal_diagnosis**: Sources disagree on Principal Diagnosis: 'Suspected colitis / Grade-I fatty liver (per discharge summary)' (p2); 'DKA (Diabetic Ketoacidosis) - per ER diagnosis and multiple consultation notes' (p3); 'Acute gastroenteritis with dehydration / Urinary tract infection (per admission note)' (p1); 'AFI, DKA, Uncontrolled T2DM, B/L pyelonephritis (per consultation notes)' (p54) (pages [1, 2, 3, 54])
- **[med_reconciliation] discharge_medications**: rabeprazole ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: ondansetron CHANGED with no documented reason (pages [2, 42])
- **[med_reconciliation] discharge_medications**: ofloxacin ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: mstrong ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: zedott ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: entr ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: mefenamic acid ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: loperamide ADDED with no documented reason (pages [2])
- **[med_reconciliation] discharge_medications**: meropenem STOPPED with no documented reason (pages [42])
- **[med_reconciliation] discharge_medications**: pantoprazole STOPPED with no documented reason (pages [42])
- **[med_reconciliation] discharge_medications**: insulin glargine STOPPED with no documented reason (pages [44])
- **[med_reconciliation] discharge_medications**: dolo STOPPED with no documented reason (pages [44])
- **[med_reconciliation] discharge_medications**: sepger STOPPED with no documented reason (pages [44])
- **[med_reconciliation] discharge_medications**: ivfluidsnsrl STOPPED with no documented reason (pages [45])
- **[med_reconciliation] discharge_medications**: sumol STOPPED with no documented reason (pages [43])
- **[med_reconciliation] discharge_medications**: insulin STOPPED with no documented reason (pages [43])
- **[med_reconciliation] medication_reconciliation**: Multiple medication changes from IV to oral at discharge without documented reasons. Notably, insulin (Actrapid and Lantus) used during admission is not listed on discharge medications - unclear if insulin was discontinued or if patient was switched to oral hypoglycemics. Also, INJ HAPPYNERVE PLUS was given during admission but not on discharge list. (pages [2, 42, 43, 44])
- **[drug_interaction] discharge_medications**: Interaction (high): Additive QT-interval prolongation risk from combining loperamide + ofloxacin + ondansetron.
- **[conflict] patient_demographics**: Gender conflict: Page 1 (admission note) refers to patient as 'she', while page 46 (admission record) says 'Patient on his Regular medication'. Patient name and age are not clearly documented in readable pages. Weight documented as 71 kg. (pages [1, 46])
- **[conflict] principal_diagnosis**: Multiple conflicting principal diagnoses across documents: (1) Discharge summary suggests suspected colitis/Grade-I fatty liver; (2) ER diagnosis was DKA; (3) Admission note says acute gastroenteritis with dehydration and UTI; (4) Multiple consultation notes document AFI, DKA, uncontrolled T2DM, and bilateral pyelonephritis as the working diagnoses. The discharge summary does not explicitly list a principal diagnosis heading. (pages [1, 2, 3, 54, 56])
- **[safety] principal_diagnosis**: Removed unverified value 'Suspected colitis / Grade-I fatty liver (per discharge summary)' (page 2): Source text mentions 'Grade-I fatty liver changes' and 'could represent colitis', but does not explicitly state 'Suspected colitis / Grade-I fatty liver' as a diagnosis. The phrase 'could represent colitis' is suggestive, not definitive. (pages [2])
- **[safety] hospital_course**: Removed unverified value 'Patient presented to ER on 26/02/2026 with fever, generalized weakness, myalgia, and was diagnosed with DKA (blood glucose 443 mg/dL) with hypotension (BP 87/50 mmHg).' (page 3): Source text shows BP 87/50 and blood glucose 443 mg/dl, and diagnosis DKA, but does not mention fever, generalized weakness, myalgia, or hypotension explicitly. Fever and weakness are not in source. (pages [3])

### MEDIUM
- **[unreadable] source_document**: Page 4 is only partially legible; some content could not be read. (pages [4])
- **[unreadable] source_document**: Page 21 is only partially legible; some content could not be read. (pages [21])
- **[unreadable] source_document**: Page 22 is only partially legible; some content could not be read. (pages [22])
- **[unreadable] source_document**: Page 23 is only partially legible; some content could not be read. (pages [23])
- **[unreadable] source_document**: Page 35 is only partially legible; some content could not be read. (pages [35])
- **[unreadable] source_document**: Page 37 is only partially legible; some content could not be read. (pages [37])
- **[unreadable] source_document**: Page 42 is only partially legible; some content could not be read. (pages [42])
- **[unreadable] source_document**: Page 43 is only partially legible; some content could not be read. (pages [43])
- **[unreadable] source_document**: Page 70 is only partially legible; some content could not be read. (pages [70])
- **[conflict] discharge_date**: Discharge date uncertainty: Consultation on 02/03/2026 says 'Discharge on Request (Evening)', but the discharge summary page index metadata says dates=09.03.2026. The follow-up review is scheduled for 09.03.2026. Clarify actual discharge date. (pages [2, 56])
- **[missing] patient_demographics**: Patient Demographics: MISSING — Patient name, age, and MRN are not documented in any readable page. Gender is conflicting (she vs his). Weight is 71 kg (documented on medication charts). (pages [1, 46, 42, 43, 44, 45])
- **[safety] hospital_course**: 'hospital_course' is a synthesized narrative; verify it against the source record before use. (pages [2, 3, 41, 42])


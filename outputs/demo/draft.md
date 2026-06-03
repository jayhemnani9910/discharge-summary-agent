# Discharge Summary — DRAFT FOR CLINICIAN REVIEW

**This is an automated draft, not a final clinical document.** Every value is cited to a source page. Fields shown as MISSING / PENDING / CONFLICT were deliberately not filled. There are 8 flag(s) requiring clinician attention (see flags.md / the Flags section).

- **Patient:** demo-patient
- **Draft status:** COMPLETE draft — all sections addressed
- **Agent steps:** 5  |  **Stop reason:** finalized by agent

---

## Patient Demographics
- Jane Doe, 52F (MRN DEMO-1)  
  _[source: page 1; confidence medium]_

## Admission Date
- 01/03/2026  
  _[source: page 1; confidence medium]_

## Discharge Date
- 05/03/2026  
  _[source: page 1; confidence medium]_

## Principal Diagnosis
**CONFLICT — sources disagree (both values shown below)**
- Acute gastroenteritis with dehydration  
  _[source: page 1; confidence medium]_
- DKA (diabetic ketoacidosis)  
  _[source: page 2; confidence medium]_

## Secondary Diagnoses
- Type 2 diabetes mellitus  
  _[source: page 1; confidence medium]_

## Hospital Course
- IV fluids and antibiotics; clinically improved  
  _[source: page 1; confidence medium]_

## Procedures
**NOT DOCUMENTED in the source notes**
_no procedures documented in the notes_

## Discharge Medications
- Raciper — 40mg 1-0-0  
  _[source: page 1]_
- Emeset — 4mg 1-1-1  
  _[source: page 1]_
- Oflox — 200mg 1-0-1  
  _[source: page 1]_

### Changes from admission (medication reconciliation)
| Medication | Change | Admission | Discharge | Reason |
|---|---|---|---|---|
| rabeprazole | continued | 40mg 1-0-0 | 40mg 1-0-0 | (unchanged) |
| ondansetron | added | - | 4mg 1-1-1 | **NO DOCUMENTED REASON — flagged** |
| ofloxacin | added | - | 200mg 1-0-1 | **NO DOCUMENTED REASON — flagged** |
| insulin | stopped | 10U | - | **NO DOCUMENTED REASON — flagged** |

_Admission medications on record:_ Insulin (10U); Raciper (40mg 1-0-0)

_Drug-interaction check run; 1 interaction(s) found (see flags). Screen used a limited mock database; absence of a result is NOT a guarantee of safety._

## Allergies
- NKDA  
  _[source: page 1; confidence medium]_

## Follow-up Instructions
- Review on 09/03/2026  
  _[source: page 1; confidence medium]_

## Pending Results
**PENDING — result awaited at time of discharge**
- Urine culture - report awaited  
  _[source: page 1; confidence medium _(unverified)_]_

## Discharge Condition
- hemodynamically stable  
  _[source: page 1; confidence medium]_


---

## Flags for clinician review

### HIGH
- **[unreadable] source_document**: Page 3 could not be transcribed; its content is unknown. (pages [3])
- **[conflict] principal_diagnosis**: Sources disagree on Principal Diagnosis: 'Acute gastroenteritis with dehydration' (p1); 'DKA (diabetic ketoacidosis)' (p2) (pages [1, 2])
- **[med_reconciliation] discharge_medications**: ondansetron ADDED with no documented reason (pages [1])
- **[med_reconciliation] discharge_medications**: ofloxacin ADDED with no documented reason (pages [1])
- **[med_reconciliation] discharge_medications**: insulin STOPPED with no documented reason (pages [2])
- **[drug_interaction] discharge_medications**: Interaction (high): Additive QT-interval prolongation risk from combining ofloxacin + ondansetron.

### MEDIUM
- **[missing] procedures**: Procedures: NOT_DOCUMENTED — no procedures documented in the notes
- **[safety] hospital_course**: 'hospital_course' is a synthesized narrative; verify it against the source record before use. (pages [1])


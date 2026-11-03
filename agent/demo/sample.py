"""Build the sample record that ships with the app: Jane Doe's complete record.

The workspace switch's "Demo patient" and "Demo clinician" open this record (see
``agent/server/sample_record.py``). Its content is transcribed from
``JANE_DOE_COMPLETE_RECORD.md``: identity and family, the care team, 21 source
documents with their originals, the current medicines, conditions and allergy,
appointments, a released GP note, an open appointment request, and 21 clinician
progress notes.

Run it to regenerate the committed files::

    python -m agent.demo.sample

which writes ``agent/demo/sample/record`` (the record folder: events, originals,
config) and ``agent/demo/sample/note-key.json`` (the key that opens its progress
notes, installed outside the record at run time, as a real record's is). Every
event is stamped on one controlled clock ending before the moment the record
describes, and written as device ``sample-record``, so nothing about the machine
that built it is committed.

The record's events carry the internal markers the clinician views read
(``demo_scenario``, ``review_dataset``, ``fictional``). Those are never shown. What
a person reads — documents, letters, notes, the profile — is written as a real
record would be, without demonstration disclaimers: the workspace switch already
says which record is open.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import files, ingest, profile, projection
from ..events import envelope
from ..demo_metadata import SCENARIO
from ..vault import Vault, scaffold
from . import document_templates as templates
from .authoring import claim, confirm
from .pathology_reports import enrich, native_results

VERSION = "jane-review-v2"
DEVICE = "sample-record"
HERE = Path(__file__).resolve().parent / "sample"
RECORD = HERE / "record"
NOTE_KEY = HERE / "note-key.json"
#: The builder's clock runs from here, a second per event, and must finish before
#: the snapshot moment the served record's clock is fixed at.
START = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)

PROFILE = {
    "full_name": "Jane Doe", "preferred_name": "Jane", "date_of_birth": "1935-11-01",
    "pronouns": "she/her", "sex_at_birth": "Female", "gender": "Woman",
    "mobile": "Not provided", "home_phone": "Facility reception: 02 5550 0100",
    "email": "jane.doe@example.invalid", "preferred_contact": "Other phone",
    "address": "Sydney Aged Care\n1 Demonstration Lane\nSydney NSW 2000",
    "postal_address": "Same as residential address",
    "emergency_name": "Emily Doe",
    "emergency_relationship": "Daughter and family carer",
    "emergency_phone": "0400 000 000", "emergency_other_phone": "Not provided",
    "kin_name": "John Doe", "kin_relationship": "Husband", "kin_phone": "Not provided",
    "kin_address": "Not provided",
    "gp_name": "Dr James Smith", "gp_practice": "Sydney Aged Care", "gp_phone": "02 5550 0101",
    "pharmacy": "Harbour Clinical Pharmacy", "pharmacy_phone": "02 5550 0102",
    "language": "English", "interpreter": "No",
    "accessibility": (
        "Usually walks to communal meals with a frame and manages eating independently "
        "(24 October 2024). Staff assist with showering. Hearing aids fitted 3 September 2025; "
        "allow extra time to answer. Prefers plain-language explanations and written "
        "medication-change information. Recent changes and temporary assistance needs are dated "
        "in the timeline; they do not replace this usual baseline."
    ),
    "advance_care": (
        "Advance directive and formal substitute decision-maker: Not provided. Emily is an "
        "involved daughter, not assumed to hold legal authority. Include her in discussions "
        "with Jane's permission."
    ),
    "allergy_notes": "Penicillin: rash reported by Jane years ago. Timing and severity are uncertain.",
    "important_notes": (
        "Lives at Sydney Aged Care. Communal meals, gardening and family visits are important "
        "to Jane. Atrial fibrillation, hypertension and knee osteoarthritis are recorded. Current "
        "directions are in the 28 August 2026 medication chart, reviewed at the GP consultation "
        "on 21 October 2026. Cardiologist: Dr Matthew Thompson, Harbourside Cardiology."
    ),
    **{key: "Not provided" for key in (
        "medicare_number", "medicare_irn", "ihi", "dva_number", "concession_number",
        "private_fund", "private_member_number")},
}

CARE_TEAM = [
    dict(name="Dr James Smith", role="Usual GP", organisation="Sydney Aged Care", facility=True,
         location="Sydney Aged Care · GP room", phone="02 5550 0101"),
    dict(name="Morgan Bell", role="Registered nurse", organisation="Sydney Aged Care", facility=True,
         location="Sydney Aged Care"),
    dict(name="Dr Olivia Bennett", role="Locum GP", organisation="Sydney Aged Care", facility=True,
         location="Sydney Aged Care · GP room"),
    dict(name="Dr Liam Wilson", role="Locum GP", organisation="Sydney Aged Care", facility=True),
    dict(name="Dr Matthew Thompson", role="Cardiologist", organisation="Harbourside Cardiology", facility=False,
         location="Harbourside Cardiology · 14 Example Wharf, Sydney"),
    dict(name="Dr Amelia Patel", role="Pharmacist", organisation="Harbour Clinical Pharmacy", facility=False),
    dict(name="Daniel Brooks", role="Podiatrist", organisation="Example Community Health", facility=False,
         location="Sydney Aged Care · allied health room"),
    dict(name="Sarah Green", role="Physiotherapist", organisation="Sydney Aged Care", facility=True,
         location="Sydney Aged Care · therapy room"),
    dict(name="Olivia Reed", role="Audiologist", organisation="Example Community Audiology", facility=False,
         location="Example Community Audiology · 2 Demonstration Road, Sydney"),
]

ORGANISATION = {m["name"]: m["organisation"] for m in CARE_TEAM}
ORGANISATION.update({"Example Pathology Laboratory": "Example Pathology Laboratory",
                     "Jane Doe": "Sydney Aged Care", "Emily Doe": "Sydney Aged Care"})

MEDICINES = {"Apixaban": "5 mg twice daily", "Metoprolol": "25 mg twice daily",
             "Amlodipine": "2.5 mg once daily", "Rosuvastatin": "5 mg nightly"}


def P(text):
    return {"type": "paragraph", "text": text}


def T(headers, rows, widths):
    return {"type": "table", "headers": headers, "rows": rows, "widths": widths}


def PLAN(*items, heading="Recommendations"):
    return {"type": "plan", "heading": heading, "items": list(items)}


def source(id, date, title, author, role, text="", *, received=None, document_date=None, **extra):
    return dict(id=id, date=date, document_date=document_date or date, received=received or date,
                title=title, author=author, role=role, text=text, **extra)


def letter_text(blocks):
    """The letter's words as one text, in the order the page shows them."""
    out = []
    for block in blocks:
        if block["type"] == "paragraph":
            out.append(block["text"])
        elif block["type"] == "table":
            out.append("\n".join([" | ".join(block["headers"])] + [" | ".join(r) for r in block["rows"]]))
        elif block["type"] == "plan":
            out.append("\n".join(f"{n}. {item}" for n, item in enumerate(block["items"], 1)))
    return "\n\n".join(out)


CARDIO_2025 = [
    P("Thank you for referring Jane for assessment. Atrial fibrillation is documented in the assessment "
      "record. Hypertension and knee osteoarthritis are also recorded. The original dates of onset of those "
      "conditions are not established."),
    P("The recommendation is for apixaban 5 mg twice daily and metoprolol 25 mg twice daily, with prescribing "
      "and reconciliation by the usual GP. This is the recommendation at the time of this assessment; a later "
      "issued prescription is a separate record."),
    P("No additional examination measurements, ECG trace or echocardiogram findings accompany this source. "
      "They have not been added to this letter."),
    PLAN("Please review the medication record and document the issued directions in the GP chart.",
         "Reassess symptoms and treatment tolerance at subsequent medical review."),
]

AUDIOLOGY = [
    P("I saw Jane at Sydney Aged Care today for a brief hearing-aid service check after she reported that "
      "speech sounded muffled through the left aid. Her daughter Emily attended with Jane's agreement. This "
      "was a troubleshooting visit."),
    P("Jane has used hearing aids since September 2025 and would like to follow conversation more easily at "
      "meals. No new audiogram was obtained today."),
    T(["Check", "Finding / action"], [
        ["External device inspection", "Left wax guard obstructed; replaced. Right wax guard clear."],
        ["Listening check", "Both aids sounded clear after servicing."],
        ["Patient response", "Jane reported clearer speech with the left aid."],
        ["Programming / audiometry", "No programming change or threshold testing undertaken."],
    ], [170, 339]),
    P("The immediate concern was consistent with a device maintenance problem. A satisfactory listening check "
      "is not a measurement of hearing thresholds and does not exclude a change in hearing. We did not "
      "undertake vestibular testing, and the reported dizziness should continue to be assessed by the "
      "medical team."),
    PLAN("Facility staff can help Jane check that the aids are seated and switched on before meals. Please "
         "report any return of muffled sound or discomfort.",
         "Continue the booked hearing-aid review on 18 November, bringing both aids and any observations "
         "about communication in daily activities.",
         "Use a quiet setting and written medication information for important discussions, allowing Jane "
         "time to respond."),
]

CARDIO_2026 = [
    P("Thank you for asking me to review Jane's ongoing dizziness in the context of atrial fibrillation and "
      "her recent blood results. I undertook a telephone case consultation with Morgan Bell, the facility "
      "registered nurse, today. This letter follows my September advice and the completed GP consultation of "
      "21 October. Jane was not examined during this case consultation."),
    P("The supplied history describes intermittent dizziness on getting up, continuing in Jane's own update "
      "of 23 October. Emily observed Jane asking for support and pausing on their walk to the dining room on "
      "24 October. Staff have subsequently recorded similar requests. Jane has also described remaining in "
      "her room for breakfast because she worries about becoming dizzy. These reports indicate a practical "
      "effect on participation and confidence; they do not establish a cardiovascular cause."),
    P("Her usual mobility is with a frame, and she normally eats independently. Atrial fibrillation was "
      "documented in May 2025, with hypertension and knee osteoarthritis also recorded. No current "
      "examination, standing blood-pressure series, pulse record or ECG accompanies this referral. I have "
      "not inferred these findings from her medication chart."),
    T(["Medicine", "Directions in the 28 August 2026 chart"], [
        ["Apixaban", "5 mg twice daily"], ["Metoprolol", "25 mg twice daily"],
        ["Amlodipine", "2.5 mg once daily"], ["Rosuvastatin", "5 mg at night"],
    ], [170, 339]),
    P("The chart records a reduction of amlodipine from 5 mg to 2.5 mg on 28 August. No subsequent issued dose "
      "change is documented. Jane's question about which heart tablet changed remains relevant to "
      "reconciliation and to a clear explanation of her current regimen. This correspondence does not issue "
      "or renew a prescription."),
    P("The full blood examination collected on 27 October reports haemoglobin 101 g/L (laboratory interval "
      "115–165), compared with 128 g/L on 14 April. The 29 October electrolyte panel reports sodium "
      "122 mmol/L (135–145), previously 138 mmol/L, with an explicit laboratory critical flag. Potassium is "
      "4.3 mmol/L (3.5–5.2) and creatinine 74 µmol/L (45–90). The reports were issued after the earlier "
      "September correspondence. There is no documented clinical assessment of these new abnormalities in "
      "the material provided."),
    P("My impression is persistent positional dizziness with reduced confidence in walking and communal meal "
      "attendance, of undetermined cause. The low sodium and lower haemoglobin warrant prompt medical "
      "assessment in their own right. It would be premature to attribute Jane's symptoms to either result, "
      "to atrial fibrillation or to a particular medicine without examination and reconciliation."),
    PLAN("Please arrange same-day in-person medical assessment of Jane and review the laboratory critical "
         "alert. The assessing team should determine the appropriate setting and repeat blood tests; a "
         "follow-up appointment alone should not substitute for this assessment.",
         "Reconcile the medication administration record with the current chart, including the amlodipine "
         "reduction. Review pulse, lying/standing blood pressure where safe, current weight and renal function "
         "when assessing treatment. No empiric dose change is issued in this letter.",
         "Assess the fall in haemoglobin and ask about possible bleeding, particularly while Jane is prescribed "
         "apixaban. Decisions about anticoagulation should follow the treating team's assessment; INR is not "
         "being requested as routine monitoring of apixaban.",
         "Please explain the recorded medication change to Jane in plain language and provide a written list, "
         "involving Emily with Jane's permission. Arrange cardiology follow-up after the immediate assessment, "
         "with earlier review if symptoms persist or the assessment identifies a cardiovascular concern."),
    P("I would appreciate the assessment findings and updated medication list so that the next cardiology "
      "review can address the outstanding symptoms with a reconciled record."),
]

PHYSIO = [
    P("I reviewed Jane at the facility today because she has been asking for someone to remain beside her "
      "when walking to meals. This was a brief bedside safety review rather than the full therapy session "
      "planned for 12 November. Her usual function is walking with her frame to communal meals and eating "
      "independently."),
    P("Jane described feeling light-headed after standing and said she was less confident about walking to "
      "breakfast. I observed one sit-to-stand from her usual chair: she used the armrests and completed the "
      "transfer with close guarding, without an observed loss of balance during that transfer. When she "
      "reported light-headedness in standing, I returned her to sitting and did not proceed with "
      "walking-distance or timed balance testing."),
    T(["Functional task", "Observation today"], [
        ["Sit-to-stand", "Armrests used; close guarding for one observed transfer."],
        ["Walking / balance tests", "Deferred after reported light-headedness."],
        ["Usual aid", "Frame, as recorded in the established care plan."],
    ], [170, 339]),
    P("The assessment is currently limited by light-headedness. No exercise progression was attempted; the "
      "immediate focus is safe participation while the medical team assesses the symptoms and recent blood "
      "results."),
    PLAN("Continue the recorded frame and provide close staff support for transfers and short essential walks "
         "according to Jane's presentation. Stop the activity and seek nursing assistance if she becomes "
         "light-headed.",
         "Defer progression of balance work or walking endurance until the medical team has assessed the "
         "dizziness and new results.",
         "Review the assistance plan with nursing staff following medical assessment. Retain the 12 November "
         "physiotherapy appointment for reassessment if Jane is able to participate safely."),
    P("I will reassess Jane's mobility and goals after medical review."),
]

PODIATRY = [
    P("I reviewed Jane at the Sydney Aged Care outreach clinic today for routine foot care and footwear advice. "
      "Jane uses a frame and has recently asked for support when walking."),
    P("On inspection there was dry skin at both heels and a small amount of superficial callus beneath the "
      "first metatarsal heads. No open lesion, discharge or focal redness was seen. The nails required minor "
      "smoothing. The heel of Jane's current slippers was loose and did not hold the foot securely. No "
      "vascular pressure measurements or formal sensory testing were undertaken during this limited visit."),
    P("I gently filed the nail edges and applied moisturiser to intact heel skin. I did not perform invasive "
      "callus debridement, and no bleeding was observed. Her charted apixaban treatment was noted; I have not "
      "advised interruption or alteration of any medicine."),
    P("The impression is routine skin and nail-care needs with footwear that could provide a more secure fit. "
      "These findings do not explain Jane's dizziness, and I have not diagnosed a vascular, neuropathic or "
      "infective foot condition from this review."),
    PLAN("Use footwear with a secure heel and fastening, checking comfort and skin after wear. Seek staff help "
         "with footwear and essential walking while Jane is unsteady.",
         "Continue regular inspection of the feet and moisturise intact dry heel skin, avoiding application "
         "between the toes. Report a new skin break, redness, discharge or pain to the nursing team.",
         "Arrange a routine podiatry review in approximately six weeks, or earlier for a new foot concern. This "
         "recommendation is not a confirmed booking."),
    P("Please let our outreach service know if footwear problems or foot discomfort are limiting Jane's "
      "participation despite these measures."),
]

SOURCES = [
    source("baseline", "2024-11-01", "Baseline health and usual function", "Dr James Smith", "Usual GP",
           "Jane usually walks to communal meals using her frame and eats independently. She enjoys gardening "
           "and family visits. Hypertension and knee osteoarthritis are recorded; original diagnosis dates are "
           "unknown. Penicillin rash was reported by Jane years ago; timing and severity are uncertain."),
    source("nurse-history", "2024-12-05", "Morgan Bell - Nursing note", "Morgan Bell", "Registered nurse",
           "Jane walks to meals with her frame and participates in the dining room. Staff assist with "
           "showering. No recent change in participation is reported."),
    source("bennett-consult", "2025-03-26", "Dr Olivia Bennett - GP note", "Dr Olivia Bennett", "Locum GP",
           "Completed consultation at 09:30 Australia/Sydney. Jane described her usual mobility with a frame. "
           "Hypertension and knee osteoarthritis were reviewed. No medication change was issued.",
           consultation=True),
    source("cardio-diagnosis", "2025-05-02", "Cardiology assessment: atrial fibrillation", "Dr Matthew Thompson",
           "Cardiologist", letter_text(CARDIO_2025), blocks=CARDIO_2025,
           subject="Assessment: atrial fibrillation"),
    source("gp-af", "2025-05-06", "GP prescription record for atrial fibrillation", "Dr James Smith", "Usual GP",
           "GP issued apixaban 5 mg twice daily and metoprolol 25 mg twice daily following the cardiology letter. "
           "Amlodipine 2.5 mg once daily continued.",
           medications=[["Apixaban", "5 mg twice daily"], ["Metoprolol", "25 mg twice daily"],
                        ["Amlodipine", "2.5 mg once daily (continued)"]]),
    source("previous-pathology", "2026-04-14", "Full blood examination and biochemistry",
           "Example Pathology Laboratory", "Laboratory"),
    source("med-chart", "2026-08-28", "Medication chart", "Dr James Smith", "Usual GP",
           "Current directions: apixaban 5 mg twice daily; metoprolol 25 mg twice daily; amlodipine 2.5 mg once "
           "daily; rosuvastatin 5 mg nightly. Amlodipine was reduced from 5 mg to 2.5 mg on this date. No later "
           "issued changes are documented.",
           medications=[[name, directions] for name, directions in MEDICINES.items()]),
    source("smith-consult", "2026-10-21", "Dr James Smith - GP note", "Dr James Smith", "Usual GP",
           "Completed consultation at 09:30 Australia/Sydney. Jane reported intermittent dizziness. Current chart "
           "reviewed: apixaban 5 mg twice daily, metoprolol 25 mg twice daily, amlodipine 2.5 mg daily and "
           "rosuvastatin 5 mg nightly. No medication change issued.", consultation=True),
    source("med-question", "2026-10-22", "Jane asks which heart tablet changed", "Jane Doe", "Patient",
           "Which heart tablet changed in August? I would like someone to explain my current medication list at "
           "my next appointment.", submission=True, issue="med-question", unresolved=True),
    source("patient-dizzy", "2026-10-23", "Jane reports persistent dizziness", "Jane Doe", "Patient",
           "I still feel dizzy at times when I get up. I would like to talk about it at my next appointment.",
           submission=True, issue="dizziness"),
    source("carer-walk", "2026-10-24", "Walking support requested", "Emily Doe", "Daughter / family carer",
           "On our walk to the dining room Jane asked me to stay beside her and paused to rest. This is what I "
           "saw on our walk, not a report from staff.", submission=True, issue="walking"),
    source("staff-walk", "2026-10-25", "Walking support requested", "Morgan Bell", "Registered nurse",
           "Jane asked me to stay nearby while she walked with her frame towards the dining room. This "
           "contribution records her request to staff.", submission=True, issue="walking"),
    source("meals", "2026-10-26", "Jane reports fewer communal breakfasts", "Jane Doe", "Patient",
           "I am staying in my room for breakfast because I worry about feeling dizzy. I miss talking to the "
           "other residents.", submission=True, issue="meals"),
    source("low-haemoglobin", "2026-10-27", "Full blood examination", "Example Pathology Laboratory",
           "Laboratory", document_date="2026-10-28", received="2026-10-28"),
    source("critical-sodium", "2026-10-29", "Electrolyte panel", "Example Pathology Laboratory", "Laboratory",
           document_date="2026-10-30", received="2026-10-30",
           laboratory_flag="Laboratory critical flag: sodium below 125 mmol/L"),
    source("stable-renal", "2026-10-29", "Renal function panel", "Example Pathology Laboratory", "Laboratory",
           document_date="2026-10-30", received="2026-10-31"),
    source("nurse-clinical", "2026-10-31", "Morgan Bell - Nursing note", "Morgan Bell", "Registered nurse",
           "Jane requested staff remain nearby while she walks with her frame. Jane said she still worries about "
           "dizziness. No fall was observed during this recorded walk. Staff recorded the request; no cause is "
           "assigned."),
    source("audiology-correspondence", "2026-11-03", "Hearing-aid service check", "Olivia Reed", "Audiologist",
           letter_text(AUDIOLOGY), blocks=AUDIOLOGY, subject="Hearing-aid service check"),
    source("cardio-recommendation", "2026-11-03", "Telephone case consultation: dizziness and recent results",
           "Dr Matthew Thompson", "Cardiologist", letter_text(CARDIO_2026), blocks=CARDIO_2026,
           subject="Telephone case consultation: dizziness and recent results", recommendation=True),
    source("physio", "2026-11-03", "Bedside functional review", "Sarah Green", "Physiotherapist",
           letter_text(PHYSIO), blocks=PHYSIO, subject="Bedside functional review"),
    source("podiatry-correspondence", "2026-11-03", "Outreach podiatry review", "Daniel Brooks", "Podiatrist",
           letter_text(PODIATRY), blocks=PODIATRY, subject="Outreach podiatry review"),
]

APPOINTMENTS = [
    ("2026-11-03", "09:30", "Dr James Smith", "GP review", "Sydney Aged Care · GP room"),
    ("2026-11-05", "09:30", "Dr James Smith", "GP appointment to discuss your report", "Sydney Aged Care · GP room"),
    ("2026-11-12", "11:00", "Sarah Green, physiotherapist", "Physiotherapy", "Sydney Aged Care · therapy room"),
    ("2026-11-18", "14:00", "Olivia Reed, audiologist", "Hearing-aid review",
     "Example Community Audiology · 2 Demonstration Road, Sydney"),
]

GP = ("Dr James Smith", "Facility GP", "james.smith")
NURSE = ("Morgan Bell", "Registered nurse", None)
BENNETT = ("Dr Olivia Bennett", "Locum GP, Sydney Aged Care", "olivia.bennett")
WILSON = ("Dr Liam Wilson", "Locum GP, Sydney Aged Care", None)

PROGRESS_NOTES = [
    ("2024-10-26", NURSE, "Routine care review",
     "Jane walked to the dining room with her frame and managed eating independently. Her preference for "
     "plain-language explanations and written medication changes is recorded. Emily may be included in "
     "discussions with Jane's permission."),
    ("2024-11-11", GP, "Blood pressure follow-up",
     "Routine follow-up of the monitoring record. Repeat observations requested before the next review. No "
     "numerical readings reproduced in this progress note. This is an ongoing-care review, not an admission "
     "assessment."),
    ("2024-12-05", NURSE, "Mobility support",
     "Physiotherapy recommendations reviewed with care staff. Frame available for walks to meals; allow rest "
     "breaks. Jane wishes to continue gardening."),
    ("2025-01-15", GP, "Monitoring and medication review",
     "Monitoring record reviewed with Jane. Written medication directions supplied on the released chart. "
     "Continue the shared monitoring plan."),
    ("2025-03-26", BENNETT, "Routine GP cover review",
     "Reviewed Jane during facility GP cover. Hypertension monitoring and usual activities discussed. Jane "
     "continues to use her frame. No medication change recorded."),
    ("2025-04-11", GP, "Palpitations review",
     "Jane describes intermittent fluttering while sitting. ECG and ambulatory monitoring arranged. Her "
     "wording retained in the patient-visible note; no rhythm diagnosis established at this visit."),
    ("2025-05-06", GP, "Cardiology recommendations reviewed",
     "Reviewed released cardiology recommendations with Jane following the atrial fibrillation assessment. "
     "Updated prescriptions and medication chart issued. Provide written explanation of each change."),
    ("2025-05-14", NURSE, "Medication communication",
     "Written medicine list kept available after pharmacist counselling. Jane values having a list to refer to "
     "when remembering changes."),
    ("2025-09-04", NURSE, "Hearing-aid support",
     "Hearing aids fitted on 3 September. Allow additional response time and check that the aids are in place "
     "for conversations. Written care instructions available."),
    ("2025-11-07", GP, "Post-discharge follow-up",
     "Reviewed pain, mobility and medicines following the fall and return from hospital. Regular medicine "
     "directions unchanged. Physiotherapy reassessment and temporary walking supervision arranged."),
    ("2025-11-14", NURSE, "Updated mobility plan",
     "Physiotherapy reassessment completed. Temporary increased supervision recorded for walking and "
     "transfers. Continue frame access and rest breaks."),
    ("2026-01-12", GP, "Recovery and confidence",
     "Reviewed recovery and assistance needs with Jane. She wants to keep attending communal meals. Continue "
     "the shared support plan as confidence improves."),
    ("2026-03-18", WILSON, "Facility GP cover review",
     "Reviewed the current chart and practical communication needs during GP cover. Jane would like tablet "
     "changes explained in writing. No prescribing change recorded."),
    ("2026-05-05", GP, "Knee pain and activities",
     "Jane reports knee pain limiting gardening. Discussed pacing, mobility support and physiotherapy goals. No "
     "new regular prescription recorded."),
    ("2026-05-20", NURSE, "Care-plan priorities",
     "Jane and Emily attended the shared care-plan discussion. Meals, gardening and family visits remain "
     "priorities. Include Emily with Jane's permission."),
    ("2026-08-20", GP, "Blood pressure medicine review",
     "Monitoring reviewed and revised amlodipine directions issued as recorded in the released prescription. "
     "Other regular directions unchanged."),
    ("2026-08-25", NURSE, "Dizziness reported",
     "Jane reports dizziness on standing after a recent tablet change. Dr Smith contacted for review. The "
     "timing is Jane's observation; a medication cause has not been established."),
    ("2026-08-26", GP, "Dizziness assessment",
     "Reviewed reported symptoms and confidence walking. Revised directions reflected in the 28 August chart. "
     "Offer walking assistance and review her symptom update."),
    ("2026-10-15", GP, "Pathology and medication list",
     "Reviewed October pathology and pharmacist report with Jane. Directions remain those in the 28 August "
     "chart. Written explanation of the August change requested."),
    ("2026-10-19", NURSE, "Walking assistance reported",
     "Emily reports that Jane asked her to stay beside her and stopped twice to rest while walking to meals. "
     "Observation attributed to Emily; GP review arranged."),
    ("2026-10-21", GP, "Walking confidence and recent symptoms",
     "Reviewed Jane's concerns about dizziness and its impact on communal meals, alongside Emily's "
     "observations. Continue shared mobility support and carry remaining medication questions into the next "
     "appointment."),
]


# ---- rendering -------------------------------------------------------------


@contextlib.contextmanager
def plain_templates():
    """The facility templates as a real record's documents would read.

    The shared templates mark everything they render as a demonstration; the
    sample record's originals are written without that, and with a patient
    identifier that does not say DEMO.
    """
    saved = templates.FOOTER, templates.PATIENT_ID, templates.FACILITIES
    templates.FOOTER = ""
    templates.PATIENT_ID = "JD-19351101"
    templates.FACILITIES = {
        name: replace(facility, phone=facility.phone.replace(" (fictional)", ""))
        for name, facility in saved[2].items()
    }
    try:
        yield
    finally:
        templates.FOOTER, templates.PATIENT_ID, templates.FACILITIES = saved


def original_pdf(item) -> bytes:
    organisation = ORGANISATION[item["author"]]
    if item.get("ordered_panel"):
        n = 1 + [s["id"] for s in SOURCES].index(item["id"])
        doc = dict(title=item["title"], event_date=item["date"], document_date=item["document_date"],
                   received_date=item["received"], results=item["results"],
                   specimen="EDTA whole blood" if item["id"] == "low-haemoglobin" else
                   "EDTA whole blood / serum" if item["id"] == "previous-pathology" else "Serum",
                   accession=f"EPL-{item['date'].replace('-', '')}-{n:03d}",
                   laboratory_flag=item.get("laboratory_flag"))
        return templates.pathology(doc)[0]
    if item.get("blocks"):
        doc = dict(item, organisation=organisation, document_date=item["document_date"])
        return templates.letter(doc)[0]
    if item.get("submission"):
        from .jane import source_pdf
        head = (f"{item['title']}\nJane Doe | DOB 1 November 1935\n"
                f"From: {item['author']} | {item['role']}\nDate: {templates.human(item['date'])}\n\n")
        return source_pdf(head + item["text"])
    doc = dict(item, organisation=organisation, document_date=item["document_date"], event_date=item["date"])
    return templates.clinical_note(doc)[0]


def native_document(item) -> dict:
    organisation = ORGANISATION[item["author"]]
    native = {
        "title": item["title"], "date": item["document_date"], "authored_date": item["document_date"],
        "received_date": item["received"], "organisation": organisation, "specialty": item["role"],
        "professional": item["author"], "recipient": "Dr James Smith, Sydney Aged Care",
        "patient": {"name": "Jane Doe", "dob": "1 November 1935"},
        "sections": [{"heading": "Clinical correspondence" if item.get("blocks") else "Document",
                      "text": item["text"]}],
        "results": [], "medications": [], "prescriptions": [], "release": None,
    }
    if item.get("medications"):
        native["medications"] = [{"medicine": name, "directions": directions}
                                 for name, directions in item["medications"]]
    if not item.get("submission"):
        native.update(facility_template="facility-documents-v1", original_document_layout=True)
    if item.get("ordered_panel"):
        native.update(ordered_panel=item["ordered_panel"], pathology_report=True,
                      results=native_results(item["results"]), sections=[])
        if item.get("laboratory_flag"):
            native["sections"] = [{"heading": "Laboratory alert", "text": item["laboratory_flag"]}]
    if item.get("consultation"):
        native["calendar_appointment"] = {"professional": item["author"], "time": "09:30",
                                          "status": "Completed", "location": "Sydney Aged Care · GP room"}
    return native


def prepared(item) -> dict:
    """Pathology rows from the shared panels, worded as the laboratory's own report."""
    item = enrich(item)
    if item.get("text"):
        item["text"] = item["text"].replace("this fictional laboratory’s", "the laboratory’s")
    return item


# ---- building --------------------------------------------------------------


class Clock:
    """One second per reading, from START; shared by the events and the record."""

    def __init__(self):
        self.at = START

    def __call__(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at

    def ts(self) -> str:
        return envelope.format_ts(self())


def build(target: Path, key_out: Path | None = None) -> Path:
    """Write the sample record to *target* (which must not exist or be empty)."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"{target} is not empty")
    clock = Clock()
    saved_now = envelope.now_ts
    with tempfile.TemporaryDirectory() as home, _environment(home), plain_templates():
        envelope.now_ts = clock.ts
        try:
            _populate(target, clock)
            if key_out is not None:
                from ..progress_notes import key_path
                Path(key_out).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(key_path(target), key_out)
        finally:
            envelope.now_ts = saved_now
    # Derived; rebuilt from the events wherever the record is opened.
    for derived in ("wiki", ".agent"):
        shutil.rmtree(target / derived, ignore_errors=True)
    return target


@contextlib.contextmanager
def _environment(home):
    from ..device import ENV_CONFIG_HOME, ENV_DEVICE

    saved = {name: os.environ.get(name) for name in (ENV_CONFIG_HOME, ENV_DEVICE)}
    os.environ[ENV_CONFIG_HOME] = home
    os.environ[ENV_DEVICE] = DEVICE
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _populate(root: Path, clock: Clock) -> None:
    from fastapi.testclient import TestClient

    from . import demo_config
    from .. import progress_notes
    from ..server.app import create_app
    from ..server.sample_record import SampleAuthStore
    from ..vault import DEMO_MARKER_FILENAME

    scaffold(root)
    files.write_text(root / "config.toml", demo_config())
    files.write_text(root / DEMO_MARKER_FILENAME,
                     "# Jane Doe\n\nThe sample record that ships with the app. It is rebuilt from "
                     "agent/demo/sample.py.\n")
    vault = Vault.open(root)
    device = vault.identity.id

    def append(type, payload, **kw):
        event = envelope.new(type, device, payload=payload, **kw)
        vault.append(event)
        return event

    append("note.recorded", {"review_dataset": VERSION, "fictional": True, "sample_record": True})
    append(profile.UPDATED, {"values": PROFILE, "demo_scenario": SCENARIO})
    append("note.recorded", {"demo_scenario": SCENARIO, "care_team": CARE_TEAM, "fictional": True})

    refs = {}
    for raw in SOURCES:
        item = prepared(raw)
        result = ingest.ingest_bytes(vault, original_pdf(item), ingest.CaptureContext(
            source="import", original_filename=item["title"] + ".pdf"))
        refs[item["id"]] = result.short
        payload = {
            **{k: v for k, v in item.items() if k not in ("blocks", "medications", "subject")},
            "review_dataset": VERSION, "demo_scenario": SCENARIO, "demo_item_id": item["id"],
            "fictional": True, "patient_visible": True,
            "access": "submission" if item.get("submission") else "clinical",
            "event_date": item["date"],
            "source_type": "Patient/carer/staff update" if item.get("submission") else "Clinical document",
            "event_type": "observation" if item.get("submission") else
                          "appointment" if item.get("consultation") else
                          "letter" if item.get("blocks") else "document",
            "artifact": result.short, "native_document": native_document(item),
            "status": "Awaiting clinician review",
        }
        append("note.recorded", payload)

    for date, time, who, title, where in APPOINTMENTS:
        append("note.recorded", {"calendar_only": True, "demo_scenario": SCENARIO, "fictional": True,
                                 "calendar_id": "sample:" + date + ":" + time,
                                 "calendar_entry": {"date": date, "time": time, "title": title,
                                                    "professional": who, "location": where,
                                                    "kind": "appointment", "status": "Booked"}})
    append("note.recorded", {"review_recipient_authorisation": {"patient": "Jane Doe", "carer": "Emily Doe"},
                             "basis": "Jane authorises Emily to receive shared documents and appointment "
                                      "requests."})

    transcribed = {"model": "transcribed-from-source", "model_rev": "none", "prompt_hash": "not-applicable"}
    for name, dose in MEDICINES.items():
        event = claim(device, "med:" + name.lower(), "dose", dose, artifact=refs["med-chart"], ts=clock.ts(),
                      occurred={"value": "2026-08-28", "precision": "day", "uncertainty_days": 0},
                      subject_name=name)
        event = replace(event, provenance={**transcribed, "artifact": event.provenance["artifact"]})
        vault.append(event)
        vault.append(confirm(device, event.id))
    for name, artifact in (("Hypertension", "baseline"), ("Knee osteoarthritis", "baseline"),
                           ("Atrial fibrillation", "cardio-diagnosis")):
        event = claim(device, "problem:" + name.lower().replace(" ", "-"), "name", name,
                      artifact=refs[artifact], ts=clock.ts(), subject_name=name)
        event = replace(event, provenance={**transcribed, "artifact": event.provenance["artifact"]})
        vault.append(event)
        vault.append(confirm(device, event.id))

    # What a clinician did in the record, done through the record's own routes so
    # every event has exactly the shape the application writes.
    app = create_app(vault, worker=False, clock=clock, sample=True)
    store = SampleAuthStore(vault.root)
    app.state.clinician_auth = store
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token, session = store.open_session("james.smith")
        client.cookies.set("care_clinician_session", token)
        headers = {"x-clinician-csrf": session["csrf"], "origin": "http://127.0.0.1"}

        def post(path, body):
            response = client.post(path, json=body, headers=headers)
            if response.status_code >= 300:
                raise RuntimeError(f"{path}: {response.status_code} {response.text}")
            return response.json()

        chart = client.get("/api/clinician/clinical-record").json()
        reported = next(r for r in chart["sections"]["allergies"] if r["id"] == "allergy:penicillin")
        post("/api/clinician/clinical-record", {
            "id": reported["id"], "revision": reported["revision"],
            "kind": "allergies", "request_id": "sample-penicillin",
            "values": {"name": "Penicillin", "reaction": "Rash", "verification": "reported",
                       "first_documented": "2024-11-01",
                       "description": "Reported by Jane years ago; timing and severity are uncertain."},
            "source_ids": ["baseline"], "basis": "Recorded from the baseline health summary of 1 November 2024.",
        })
        # Reviewed and shared with Jane and Emily: her GP's summary and chart, the
        # letter that diagnosed atrial fibrillation, and the latest GP note. Her
        # own view shows only what was released, so these are what put her
        # medicines and conditions on her home page.
        for issue, message in (
            ("baseline", "Your baseline health summary is now available."),
            ("cardio-diagnosis", "Dr Thompson's cardiology letter from May 2025 is now available."),
            ("gp-af", "Your prescription record for atrial fibrillation is now available."),
            ("med-chart", "Your current medication chart is now available."),
            ("smith-consult", "Your GP note from 21 October is now available."),
        ):
            post(f"/api/clinician/issues/{issue}/workflow",
                 {"kind": "review", "sources": [issue], "request_id": "sample-review-" + issue})
            post(f"/api/clinician/issues/{issue}/workflow",
                 {"kind": "release", "source": issue, "recipients": ["carer", "patient"], "message": message,
                  "confirmed": True, "request_id": "sample-release-" + issue})
        post("/api/clinician/issues/critical-sodium/workflow",
             {"kind": "appointment_request", "recipients": ["carer", "patient"], "timeframe": "within 1 week",
              "message": "Dr Smith is requesting to meet you this week", "confirmed": True,
              "request_id": "sample-request-sodium"})

        state = app.state.record
        for written, (author, role, username), title, text in PROGRESS_NOTES:
            content = {"author": author, "role": role, "written": written, "text": title + "\n\n" + text}
            if username:
                content["username"] = username
            progress_notes.store(state, content, request_id="sample:" + written + ":" + title)

    projection.rebuild(vault)
    data = {"sources": [dict(id=s["id"], artifact=refs[s["id"]], event_date=s["date"],
                             document_date=s["document_date"], received_date=s["received"], title=s["title"])
                        for s in SOURCES]}
    files.write_text(root / "DATASET-MANIFEST.json", json.dumps({"version": VERSION, **data}, indent=2) + "\n")


def main() -> None:
    staging = Path(tempfile.mkdtemp(prefix="sample-record-"))
    record = staging / "record"
    key = staging / "note-key.json"
    build(record, key)
    if RECORD.exists():
        shutil.rmtree(RECORD, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(record, RECORD)
    shutil.copyfile(key, NOTE_KEY)
    for path in RECORD.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o644)
    shutil.rmtree(staging, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    print(f"wrote {RECORD} and {NOTE_KEY}")


if __name__ == "__main__":
    main()

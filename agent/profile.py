"""Patient-entered profile details, replayed from the append-only event log.

Administrative details and explicitly labelled patient notes are separate from
reconciled clinical claims. Nothing here infers allergies or clinical status.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable
from .events.envelope import Event

UPDATED = "patient.profile.updated"


def field(key, label, kind="text", *, options=None, autocomplete=None):
    item = {"key": key, "label": label, "type": kind,
            "max_length": 4000 if kind == "textarea" else 300}
    if options:
        item["options"] = options
    if autocomplete:
        item["autocomplete"] = autocomplete
    return item


SECTIONS = [
    {"id": "identity", "title": "Personal details", "description": "The name and details you want your care team to use.", "fields": [
        field("full_name", "Full name", autocomplete="name"),
        field("preferred_name", "Preferred name / nickname", autocomplete="nickname"),
        field("date_of_birth", "Date of birth", "date", autocomplete="bday"),
        field("pronouns", "Pronouns"),
        field("sex_at_birth", "Sex recorded at birth", "select", options=["Female", "Male", "Intersex", "Prefer not to say"]),
        field("gender", "Gender identity"),
    ]},
    {"id": "contact", "title": "Contact & address", "description": "How to reach you and where correspondence should go.", "fields": [
        field("mobile", "Mobile phone", "tel", autocomplete="tel"),
        field("home_phone", "Other phone", "tel"),
        field("email", "Email address", "email", autocomplete="email"),
        field("preferred_contact", "Preferred contact method", "select", options=["Mobile phone", "SMS", "Email", "Other phone", "Post"]),
        field("address", "Residential address", "textarea", autocomplete="street-address"),
        field("postal_address", "Postal address (if different)", "textarea"),
    ]},
    {"id": "emergency", "title": "Emergency contact", "description": "The person to contact in an emergency.", "fields": [
        field("emergency_name", "Contact name"), field("emergency_relationship", "Relationship"),
        field("emergency_phone", "Phone number", "tel"), field("emergency_other_phone", "Alternate phone", "tel"),
    ]},
    {"id": "next_of_kin", "title": "Next of kin", "description": "Complete this if your next of kin differs from your emergency contact.", "fields": [
        field("kin_name", "Next of kin name"), field("kin_relationship", "Relationship"),
        field("kin_phone", "Phone number", "tel"), field("kin_address", "Address", "textarea"),
    ]},
    {"id": "cards", "title": "Medicare & healthcare cards", "description": "Your healthcare identifiers and cover. All fields are optional.", "fields": [
        field("medicare_number", "Medicare number"), field("medicare_irn", "Individual reference number (IRN)"),
        field("medicare_expiry", "Medicare expiry", "month"), field("ihi", "Individual Healthcare Identifier (IHI)"),
        field("dva_number", "DVA file number"),
        field("dva_card", "DVA card type", "select", options=["Gold", "White", "Orange"]),
        field("dva_expiry", "DVA card expiry", "date"),
        field("concession_type", "Concession card type", "select", options=["Pensioner Concession Card", "Health Care Card", "Commonwealth Seniors Health Card", "Other"]),
        field("concession_number", "Concession card number"), field("concession_expiry", "Concession expiry", "date"),
        field("private_fund", "Private health fund"), field("private_member_number", "Membership number"),
    ]},
    {"id": "care", "title": "Care team & preferences", "description": "The people and practical details that help your care run smoothly.", "fields": [
        field("gp_name", "Usual GP"), field("gp_practice", "GP practice"), field("gp_phone", "Practice phone", "tel"),
        field("pharmacy", "Usual pharmacy"), field("pharmacy_phone", "Pharmacy phone", "tel"),
        field("language", "Preferred language"),
        field("interpreter", "Interpreter required", "select", options=["Yes", "No"]),
        field("accessibility", "Accessibility / communication needs", "textarea"),
        field("advance_care", "Advance care directive / substitute decision-maker", "textarea"),
    ]},
    {"id": "notes", "title": "Allergies, reactions & other notes", "description": "Details you enter here are labelled as patient-entered notes. Confirmed clinical entries remain linked to their sources in your record.", "fields": [
        field("allergy_notes", "Patient-entered allergies / adverse reactions", "textarea"),
        field("important_notes", "Other information for your care team", "textarea"),
    ]},
]
FIELDS = {item["key"]: item for section in SECTIONS for item in section["fields"]}


def read(events: Iterable[Event]):
    values = {key: "" for key in FIELDS}
    revision = updated_at = None
    for event in events:
        if event.type != UPDATED or event.actor != "user":
            continue
        changes = event.payload.get("values")
        if not isinstance(changes, dict):
            continue
        for key, value in changes.items():
            if key in FIELDS and isinstance(value, str):
                values[key] = value
        revision, updated_at = event.id, event.ts
    return {"values": values, "revision": revision, "updated_at": updated_at}


def validate(values, today: date):
    if not isinstance(values, dict) or not values or set(values) - FIELDS.keys():
        raise ValueError("Send recognised patient profile fields.")
    clean = {}
    for key, value in values.items():
        spec = FIELDS[key]
        if not isinstance(value, str) or len(value) > spec["max_length"]:
            raise ValueError(f'{spec["label"]} must be text of at most {spec["max_length"]} characters.')
        value = value.strip()
        if value and spec.get("options") and value not in spec["options"]:
            raise ValueError(f'Choose a listed option for {spec["label"]}.')
        if value and spec["type"] in ("date", "month"):
            try:
                parsed = date.fromisoformat(value + "-01" if spec["type"] == "month" else value)
                canonical = parsed.isoformat()[:7] if spec["type"] == "month" else parsed.isoformat()
                if canonical != value:
                    raise ValueError("Non-canonical date")
            except ValueError:
                raise ValueError(f'Enter a valid date for {spec["label"]}.') from None
            if key == "date_of_birth" and parsed > today:
                raise ValueError("Date of birth cannot be in the future.")
        if value and spec["type"] == "email" and ("@" not in value or any(c.isspace() for c in value)):
            raise ValueError("Enter a valid email address.")
        clean[key] = value
    return clean

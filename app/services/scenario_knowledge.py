import re
from typing import Any

from app.services.scenario_templates import normalize_scenario

SCENARIO_KNOWLEDGE: dict[str, list[dict[str, Any]]] = {
    "correct_malo_id": [
        {"question": "Why are you calling?", "answer": "The registration could not be completed automatically, so Nomos is calling to confirm the correct market location number.", "tags": ["purpose", "clarification"]},
        {"question": "Which address is this about?", "answer": "This is about the customer address on the case details provided to the call agent.", "tags": ["address", "clarification"]},
        {"question": "What do you need from us?", "answer": "Nomos needs the correct market location number and the next step for the registration.", "tags": ["purpose", "malo"]},
        {"question": "What is wrong with the current market location number?", "answer": "The current market location number did not allow the registration to complete automatically, so it needs to be checked or corrected by the grid operator.", "tags": ["malo", "clarification"]},
        {"question": "Can you repeat the market location number you have?", "answer": "The market location number currently on the case can be repeated from the case details.", "tags": ["malo"]},
        {"question": "What happens after I give you the correct number?", "answer": "Nomos will record the corrected number so the registration can be updated and retried or handled according to the operator's instruction.", "tags": ["malo", "next_step"]},
        {"question": "Are you an AI?", "answer": "Yes, this is an AI assistant calling on behalf of Nomos to clarify this registration case.", "tags": ["ai_disclosure"]},
        {"question": "Can you send this by email instead?", "answer": "Nomos can note that the operator prefers email follow-up if the needed information cannot be completed on this call.", "tags": ["email", "next_step"]},
        {"question": "I already gave you the number.", "answer": "Acknowledge that the operator already provided the number and avoid asking for it again unless confirmation is still needed.", "tags": ["malo", "frustration"]},
        {"question": "Please hold.", "answer": "Acknowledge the hold request and wait without continuing to ask questions.", "tags": ["hold"]},
    ],
    "meter_status_clarification": [
        {"question": "Why are you calling?", "answer": "Nomos could not complete the registration automatically, so we need to confirm whether the electricity meter is still usable for the registration.", "tags": ["purpose", "meter_status", "clarification"]},
        {"question": "Which meter is this about?", "answer": "This is about the meter number shown in the case details.", "tags": ["meter_status"]},
        {"question": "Which address is this about?", "answer": "This is about the customer address on the case details provided to the call agent.", "tags": ["address", "clarification"]},
        {"question": "What do you need from us?", "answer": "Nomos needs to know whether the meter is active, inactive, removed, or temporary, and whether registration can continue or the customer needs to provide current meter details.", "tags": ["purpose", "meter_status"]},
        {"question": "What does inactive meter mean?", "answer": "It means the meter can no longer be used for the current registration, for example because it was deactivated or replaced.", "tags": ["meter_status", "clarification"]},
        {"question": "What happens if the meter is active?", "answer": "If the meter is active and matches the address, Nomos can retry or continue the registration.", "tags": ["meter_status", "next_step"]},
        {"question": "What happens if the meter is inactive or removed?", "answer": "Nomos should contact the customer for current meter details or send the case to manual review.", "tags": ["meter_status", "next_step"]},
        {"question": "What if it was only temporary?", "answer": "If it was only a temporary meter and is no longer usable, Nomos should ask the customer for the current active meter details.", "tags": ["meter_status", "next_step"]},
        {"question": "What if it was only a temporary construction meter?", "answer": "If it was only a temporary meter and is no longer usable, Nomos should ask the customer for the current active meter details.", "tags": ["meter_status"]},
        {"question": "What happens if the meter is removed?", "answer": "If the meter was removed, Nomos should contact the customer for current meter details or send the case to manual review.", "tags": ["meter_status", "next_step"]},
        {"question": "Should Nomos contact the customer?", "answer": "Nomos can contact the customer if the operator confirms the meter is inactive, removed, or not suitable for registration.", "tags": ["meter_status", "next_step"]},
        {"question": "Can the registration continue?", "answer": "If the meter is active and matches the address, Nomos can retry or continue the registration.", "tags": ["meter_status", "next_step"]},
        {"question": "Are you an AI?", "answer": "Yes, this is an AI assistant calling on behalf of Nomos to clarify this registration case.", "tags": ["ai_disclosure"]},
        {"question": "Can you repeat the meter number?", "answer": "The meter number currently on the case can be repeated from the case details.", "tags": ["meter_status"]},
        {"question": "Please hold.", "answer": "Acknowledge the hold request and wait without continuing to ask questions.", "tags": ["hold"]},
        {"question": "I do not understand why you are calling.", "answer": "Briefly explain that Nomos is checking the meter status because the registration could not be completed automatically.", "tags": ["purpose", "clarification"]},
    ],
}

_TAG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("purpose", re.compile(r"\b(why|calling|reason|what\s+is\s+this)\b", re.I)),
    ("address", re.compile(r"\b(address|location)\b", re.I)),
    ("meter_status", re.compile(r"\b(meter|inactive|removed|temporary|construction)\b", re.I)),
    ("malo", re.compile(r"\b(malo|ma\s*lo|market\s+location|number)\b", re.I)),
    ("ai_disclosure", re.compile(r"\b(ai|robot|human)\b", re.I)),
    ("email", re.compile(r"\bemail\b", re.I)),
    ("hold", re.compile(r"\b(hold|wait|second)\b", re.I)),
]

GENERAL_TAGS = {"purpose", "clarification"}


def matched_knowledge_tags(operator_text: str) -> list[str]:
    return [tag for tag, pattern in _TAG_PATTERNS if pattern.search(operator_text or "")]


def get_relevant_knowledge(scenario: str, operator_text: str, limit: int = 3) -> list[dict[str, Any]]:
    entries = SCENARIO_KNOWLEDGE.get(normalize_scenario(scenario), [])
    tags = matched_knowledge_tags(operator_text)
    selected: list[dict[str, Any]] = []
    if tags:
        selected = [entry for entry in entries if set(entry.get("tags", [])) & set(tags)]
    else:
        selected = [entry for entry in entries if set(entry.get("tags", [])) & GENERAL_TAGS]
    return selected[:limit]

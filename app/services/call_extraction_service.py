import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings
from app.database import SessionLocal
from app.models import Call, CallEvent, CallExtraction, CallState, CallTranscript
from app.services.scenario_templates import get_scenario, normalize_scenario
from app.settings_service import SettingsService

logger = logging.getLogger(__name__)

OUTCOMES = {"resolved", "unresolved", "in_progress", "needs_manual_review"}
METER_STATUS_NEXT_ACTIONS = {"notify_customer_by_email", "retry_registration", "needs_manual_review"}
MALO_NEXT_ACTIONS = {"update_malo_and_retry_registration", "needs_manual_review"}


def _state_dict(state: CallState | None) -> dict[str, Any]:
    if not state:
        return {}
    fields = [
        "phase", "language", "known_operator_name", "known_market_location_number",
        "corrected_market_location_number", "partial_malo_digits", "meter_status",
        "reference_number", "next_action", "registration_status", "last_operator_intents",
    ]
    return {field: getattr(state, field, None) for field in fields}


def _transcript_payload(turns: list[CallTranscript]) -> list[dict[str, Any]]:
    return [
        {
            "speaker": turn.speaker,
            "text": turn.text,
            "language": turn.language,
            "source": turn.source,
            "confidence": turn.confidence,
            "timestamp_ms": turn.timestamp_ms,
        }
        for turn in turns
    ]


def _base_schema(scenario: str) -> dict[str, Any]:
    base = {
        "scenario": scenario,
        "outcome": "resolved | unresolved | in_progress | needs_manual_review",
        "confidence": 0.0,
        "confirmed_facts": [],
        "uncertain_facts": [],
        "next_action": "string",
        "plain_language_note": "string",
    }
    if scenario == "meter_status_clarification":
        base.update(
            {
                "meter_number": None,
                "meter_status": "active | inactive | removed | temporary | unknown",
                "temporary_meter": None,
                "construction_meter": None,
                "meter_inactive_reason": None,
                "customer_contact_required": None,
                "reference_number": None,
                "registration_can_continue": None,
                "next_action": "retry_registration | notify_customer_by_email | needs_manual_review",
            }
        )
    elif scenario == "correct_malo_id":
        base.update(
            {
                "original_market_location_number": None,
                "corrected_market_location_number": None,
                "registration_status": None,
                "reference_number": None,
                "next_action": "update_malo_and_retry_registration | needs_manual_review",
            }
        )
    return base


def _prompt_payload(call: Call, turns: list[CallTranscript], events: list[CallEvent]) -> dict[str, Any]:
    case = call.case
    scenario = normalize_scenario(case.scenario)
    template = get_scenario(scenario)
    return {
        "task": "Post-call extraction. Read the complete call transcript and return clean JSON only.",
        "schema": _base_schema(scenario),
        "scenario": {"key": scenario, **template},
        "case_details": {
            "case_id": case.id,
            "external_case_id": case.external_case_id,
            "customer_name": case.customer_name,
            "customer_email": case.customer_email,
            "customer_address": case.customer_address,
            "meter_number": case.meter_number,
            "market_location_number": case.market_location_number,
            "problem_description": case.problem_description,
            "required_outcome": case.required_outcome,
        },
        "call_state": _state_dict(call.state),
        "known_extracted_fields": _state_dict(call.state),
        "transcript_turns": _transcript_payload(turns),
        "call": {"id": call.id, "status": call.status, "started_at": str(call.started_at), "ended_at": str(call.ended_at)},
        "event_summary": [{"event_type": e.event_type, "payload": e.event_payload} for e in events[-20:]],
        "instructions": [
            "Infer the outcome based on the call purpose, scenario, required outcome, and full transcript.",
            "Do not over-trust partial, noisy, or contradictory transcript snippets; separate confirmed_facts from uncertain_facts.",
            "Use null when a value is not supported by the transcript or case details.",
            "Include a concise plain-language note for back office.",
            "For meter_status_clarification, if the operator says only temporary or temporary meter, set temporary_meter=true and meter_status=temporary.",
            "For meter_status_clarification, set customer_contact_required=true if the operator says contact by email or says Nomos should contact the customer.",
            "For meter_status_clarification, only set construction_meter=true if the operator explicitly says construction, building phase, building site, temporary construction meter, Baustrom, or similar.",
            "If the operator only says temporary, write temporary meter, not temporary construction meter.",
        ],
    }


def _deterministic_extract(call: Call, turns: list[CallTranscript]) -> dict[str, Any]:
    case = call.case
    scenario = normalize_scenario(case.scenario)
    text = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
    low = text.lower()
    data: dict[str, Any] = {
        "scenario": scenario,
        "outcome": "needs_manual_review",
        "confidence": 0.45,
        "confirmed_facts": [],
        "uncertain_facts": [],
        "next_action": "needs_manual_review",
        "plain_language_note": "Extraction was generated locally because the LLM was unavailable; please review the transcript.",
        "reference_number": None,
    }
    if scenario == "meter_status_clarification":
        temporary = bool(re.search(r"only temporary|temporary meter|temporary|provisorisch|tempor", low))
        construction = bool(re.search(r"construction|building phase|building site|baustrom|temporary construction", low))
        removed = "removed" in low or "ausgebaut" in low
        inactive = bool(re.search(r"no longer active|inactive|not active|inaktiv", low))
        active = bool(re.search(r"still active|is active|active", low)) and not inactive
        contact = bool(re.search(r"contact .*customer|customer.*contact|by email|email|e-mail|nomos should contact", low))
        status = "temporary" if temporary else "removed" if removed else "inactive" if inactive else "active" if active else "unknown"
        data.update({
            "meter_number": case.meter_number,
            "meter_status": status,
            "temporary_meter": True if temporary else None,
            "construction_meter": True if construction else False if temporary else None,
            "meter_inactive_reason": "temporary meter" if temporary else None,
            "registration_can_continue": True if active else False if status in {"inactive", "removed"} or (temporary and contact) else None,
            "customer_contact_required": False if active else True if status in {"inactive", "removed"} or contact else None,
            "next_action": "notify_customer_by_email" if status in {"inactive", "removed"} or contact else "retry_registration" if active else "needs_manual_review",
            "outcome": "resolved" if status in {"inactive", "removed", "active"} or (temporary and contact) else "needs_manual_review",
            "plain_language_note": "Operator indicated this was a temporary meter. Nomos should contact the customer by email for current meter details." if temporary and contact else "Review transcript for meter status and next action.",
        })
        data["confirmed_facts"] = [f"meter_status={status}"] if status != "unknown" else []
    else:
        digits = re.findall(r"\b\d{10,14}\b", text)
        corrected = digits[-1] if digits else None
        data.update({
            "original_market_location_number": case.market_location_number,
            "corrected_market_location_number": corrected,
            "registration_status": "in_progress" if re.search(r"in progress|open|ongoing", low) else None,
            "next_action": "update_malo_and_retry_registration" if corrected and re.search(r"retry|resend|again|erneut", low) else "needs_manual_review",
            "outcome": "resolved" if corrected else "needs_manual_review",
            "plain_language_note": "Operator provided a corrected market location number; retry registration if confirmed." if corrected else "No corrected market location number was confidently extracted.",
        })
    return data


def _clean_result(call: Call, data: dict[str, Any]) -> dict[str, Any]:
    scenario = normalize_scenario(call.case.scenario)
    data["scenario"] = scenario
    if data.get("outcome") not in OUTCOMES:
        data["outcome"] = "needs_manual_review"
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    if scenario == "meter_status_clarification":
        status = data.get("meter_status")
        if status == "active":
            data["registration_can_continue"] = True if data.get("registration_can_continue") is None else data.get("registration_can_continue")
            data["customer_contact_required"] = False if data.get("customer_contact_required") is None else data.get("customer_contact_required")
            if data.get("next_action") in {None, "needs_manual_review"}:
                data["next_action"] = "retry_registration"
        elif status in {"inactive", "removed"}:
            data["registration_can_continue"] = False
            data["customer_contact_required"] = True
            if data.get("next_action") in {None, "needs_manual_review"}:
                data["next_action"] = "notify_customer_by_email"
        elif status == "temporary" and data.get("customer_contact_required") is True:
            data["registration_can_continue"] = False
            data["next_action"] = "notify_customer_by_email"
        if data.get("next_action") not in METER_STATUS_NEXT_ACTIONS:
            data["next_action"] = "needs_manual_review"
    elif scenario == "correct_malo_id" and data.get("next_action") not in MALO_NEXT_ACTIONS:
        data["next_action"] = "needs_manual_review"
    data.setdefault("plain_language_note", "")
    return data


def _save_extraction(db, call: Call, data: dict[str, Any]) -> None:
    column_names = {column.name for column in CallExtraction.__table__.columns} - {"id", "created_at", "extracted_json"}
    kwargs = {key: value for key, value in data.items() if key in column_names}
    extraction = CallExtraction(call_id=call.id, case_id=call.case_id, extracted_json=data, **kwargs)
    db.add(extraction)
    if call.case:
        if data.get("outcome") == "resolved":
            call.case.status = "resolved"
        elif data.get("next_action") == "needs_manual_review" or data.get("outcome") == "needs_manual_review":
            call.case.status = "needs_manual_review"
        else:
            call.case.status = data.get("outcome") or call.case.status
        db.add(call.case)
    db.commit()


def extract_call_result(call_id: int) -> dict:
    logger.warning("NOMOS_POST_CALL_EXTRACTION_STARTED call_id=%s", call_id)
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if not call:
            raise ValueError(f"Call {call_id} not found")
        turns = db.query(CallTranscript).filter_by(call_id=call_id).order_by(CallTranscript.created_at.asc()).all()
        events = db.query(CallEvent).filter_by(call_id=call_id).order_by(CallEvent.created_at.asc()).all()
        settings = SettingsService(db, get_settings().app_encryption_key)
        key = settings.get("openai_api_key")
        data = None
        if key:
            payload = _prompt_payload(call, turns, events)
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": settings.get("openai_model", "gpt-4.1-mini"),
                        "temperature": 0,
                        "messages": [
                            {"role": "system", "content": "You extract post-call structured clearing outcomes. Return only valid JSON."},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                data = json.loads(response.json()["choices"][0]["message"]["content"])
        if not isinstance(data, dict):
            data = _deterministic_extract(call, turns)
        data = _clean_result(call, data)
        logger.warning("NOMOS_POST_CALL_EXTRACTION_RESULT call_id=%s result=%s", call_id, data)
        _save_extraction(db, call, data)
        logger.warning("NOMOS_POST_CALL_EXTRACTION_COMPLETED call_id=%s", call_id)
        return data
    except Exception:
        logger.exception("NOMOS_POST_CALL_EXTRACTION_FAILED call_id=%s", call_id)
        raise
    finally:
        db.close()

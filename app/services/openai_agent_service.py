import json
import logging
import re
from typing import Any

import httpx

from app.agents.clearing_agent import ALLOWED_PHASES, LIVE_VOICE_SYSTEM_PROMPT, digit_words, normalize_spoken_digits, similar
from app.models import Call, CallState, CallTranscript

logger = logging.getLogger(__name__)

INTENTS = {
    "provides_information", "asks_clarification", "asks_to_wait", "expresses_frustration",
    "repeats_information", "corrects_previous_information", "partial_number_continuation",
    "irrelevant_or_noise", "confirms_yes", "denies_no", "asks_human_like_clarification",
}
RESPONSE_FIELDS = {"spoken_reply", "phase", "extracted_updates", "should_speak", "should_end_call", "reason"}
UPDATE_FIELDS = ["registration_status", "corrected_market_location_number", "meter_status", "reference_number", "next_action"]
VALID_MODES = {"llm_led", "deterministic_safe"}
MALO_LENGTH = 11


def _state_attr(state: CallState, name: str, default: Any = None) -> Any:
    return getattr(state, name, default) if hasattr(state, name) else default


def _set_state_attr(state: CallState, name: str, value: Any) -> None:
    if hasattr(state, name):
        setattr(state, name, value)


def _asdict_state(state: CallState) -> dict[str, Any]:
    fields = [
        "phase", "language", "known_operator_name", "known_market_location_number", "registration_status",
        "corrected_market_location_number", "partial_malo_digits", "meter_status", "reference_number",
        "next_action", "hold_mode", "last_operator_intents", "last_agent_question", "waiting_for_field",
    ]
    return {k: _state_attr(state, k) for k in fields}


def _empty_updates() -> dict[str, None]:
    return {field: None for field in UPDATE_FIELDS}


class OpenAIAgentService:
    def __init__(self, settings):
        self.settings = settings

    async def test(self):
        key = self.settings.get('openai_api_key')
        if not key:
            return False, 'Missing OpenAI API key'
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get('https://api.openai.com/v1/models', headers={'Authorization': f'Bearer {key}'})
            return r.status_code < 400, f'Status {r.status_code}'

    def get_or_create_state(self, db, call: Call, language: str | None = None) -> CallState:
        state = db.query(CallState).filter_by(call_id=call.id).first()
        if not state:
            state = CallState(call_id=call.id, phase="opening", language=language or call.active_language or call.case.preferred_language,
                              known_market_location_number=call.case.market_location_number)
            db.add(state); db.commit(); db.refresh(state)
        elif language and state.language != language:
            state.language = language; db.add(state); db.commit(); db.refresh(state)
        return state

    async def respond(self, db, call: Call, transcript: str, language: str):
        state = self.get_or_create_state(db, call, language)
        turns = db.query(CallTranscript).filter_by(call_id=call.id).order_by(CallTranscript.created_at.desc()).limit(10).all()[::-1]
        before = _asdict_state(state)
        logger.warning("NOMOS_KNOWN_FIELDS_BEFORE %s", before)
        intent = self._classify_intent(transcript, state)
        logger.warning("NOMOS_OPERATOR_INTENT intent=%s field=%s", ",".join(intent["intents"]), intent.get("field"))
        self._apply_intent_updates(db, state, transcript, intent)

        mode = self.settings.get("conversation_mode", "llm_led") or "llm_led"
        if mode not in VALID_MODES:
            mode = "llm_led"
        logger.warning("NOMOS_AGENT_MODE mode=%s", mode)
        logger.warning("NOMOS_AGENT_INPUT_STATE phase=%s", state.phase)
        logger.warning("NOMOS_AGENT_INPUT_HISTORY_TURNS count=%s", len(turns))

        if _state_attr(state, "hold_mode", False) and not (set(intent["intents"]) & {"provides_information", "asks_clarification", "expresses_frustration", "confirms_yes", "denies_no", "partial_number_continuation"}):
            result = {"spoken_reply": "", "phase": state.phase, "extracted_updates": _empty_updates(), "should_speak": False, "should_end_call": False, "reason": "hold mode ignores silence or noise"}
        elif mode == "deterministic_safe":
            result = self._policy(call, state, transcript, intent)
        else:
            result = await self._llm_or_policy(call, state, turns, transcript, language, intent)
            result = await self._anti_loop(db, call, state, turns, transcript, language, result, intent)
            result = await self._style_adapter(call, state, turns, transcript, language, result, intent)

        old = state.phase
        new = result.get("phase") if result.get("phase") in ALLOWED_PHASES else old
        updates = result.get("extracted_updates") or {}
        for field in UPDATE_FIELDS:
            if updates.get(field):
                current = _state_attr(state, field)
                if current and current != updates[field]:
                    logger.warning("NOMOS_FIELD_ALREADY_KNOWN field=%s old=%s new=%s", field, current, updates[field])
                _set_state_attr(state, field, updates[field])
        state.phase = new
        state.last_agent_question = result.get("spoken_reply") or state.last_agent_question
        _set_state_attr(state, "last_operator_intents", ",".join(intent["intents"]))
        if old != new:
            logger.warning("NOMOS_CALL_PHASE_CHANGED old=%s new=%s", old, new)
        logger.warning("NOMOS_AGENT_OUTPUT phase=%s should_speak=%s", new, result.get("should_speak"))
        logger.warning("NOMOS_AGENT_EXTRACTED_UPDATES %s", updates)
        db.add(state); db.commit(); db.refresh(state)
        logger.warning("NOMOS_KNOWN_FIELDS_AFTER %s", _asdict_state(state))
        return result

    def _classify_intent(self, transcript: str, state) -> dict[str, Any]:
        text = (transcript or "").lower().strip()
        intents: set[str] = set()
        field = None; value = None
        digits = normalize_spoken_digits(text)
        if re.search(r"hold on|give me a second|i'?m checking|please hold|wait|moment|one second|warte|augenblick", text): intents.add("asks_to_wait")
        if re.search(r"what are you talking about|what do you mean|what do you want|why are you calling|huh|clarify", text): intents.update({"asks_clarification", "asks_human_like_clarification"})
        if re.search(r"already told|you'?re fired|why are you asking again|annoying|stupid|nonsense|stop asking|what do you want", text): intents.add("expresses_frustration")
        if re.search(r"already told|as i said|again", text): intents.add("repeats_information")
        if re.search(r"no[, ]+that'?s wrong|start again|correction|correct that|not .* but", text): intents.add("corrects_previous_information")
        if re.fullmatch(r"(yes|yeah|correct|right|that'?s correct|ja|richtig|stimmt|ok|okay)[.! ]*", text): intents.add("confirms_yes")
        if re.fullmatch(r"(no|nope|nein)[.! ]*", text): intents.add("denies_no")
        if digits and (len(digits) < MALO_LENGTH or _state_attr(state, "partial_malo_digits")): intents.add("partial_number_continuation")
        if re.search(r"in progress|ongoing|still open|open|bearbeitung|läuft", text):
            intents.add("provides_information"); field = "registration_status"; value = "in_progress"
        elif re.search(r"reject|rejected|ablehn", text):
            intents.add("provides_information"); field = "registration_status"; value = "rejected"
        elif re.search(r"ready|resend|send again|erneut senden", text):
            intents.add("provides_information"); field = "next_action"; value = "resend_registration"
        elif digits:
            intents.add("provides_information"); field = "corrected_market_location_number" if len(digits) >= MALO_LENGTH else "partial_malo_digits"; value = digits
        if not intents: intents.add("irrelevant_or_noise")
        return {"intents": sorted(intents), "field": field, "value": value, "digits": digits}

    def _apply_intent_updates(self, db, state, transcript: str, intent: dict[str, Any]) -> None:
        intents = set(intent["intents"]); old_partial = state.partial_malo_digits or ""
        if "asks_to_wait" in intents:
            state.phase = "operator_checking"; _set_state_attr(state, "hold_mode", True); logger.warning("NOMOS_HOLD_MODE_ENTERED")
        if _state_attr(state, "hold_mode", False) and re.search(r"okay|ok|i found it|here it is|yes|ja", (transcript or "").lower()):
            _set_state_attr(state, "hold_mode", False); logger.warning("NOMOS_HOLD_MODE_EXITED")
        if "corrects_previous_information" in intents and re.search(r"start again|wrong", (transcript or "").lower()):
            state.partial_malo_digits = None
        if intent.get("field") == "registration_status" and intent.get("value"):
            if _state_attr(state, "registration_status"):
                logger.warning("NOMOS_FIELD_ALREADY_KNOWN field=registration_status")
            _set_state_attr(state, "registration_status", intent["value"])
        if intent.get("field") == "next_action" and intent.get("value"):
            state.next_action = intent["value"]
        digits = intent.get("digits") or ""
        if digits and "partial_number_continuation" in intents:
            combined = digits if len(digits) >= MALO_LENGTH and re.search(r"start again|full|whole", (transcript or "").lower()) else (old_partial + digits)
            if len(combined) >= MALO_LENGTH:
                state.corrected_market_location_number = combined[:MALO_LENGTH]
                state.partial_malo_digits = combined[MALO_LENGTH:] or None
                state.phase = "confirming_malo_number"; state.waiting_for_field = "malo_confirmation"
            else:
                state.partial_malo_digits = combined; state.phase = "collecting_malo_number"
            logger.warning("NOMOS_MALO_PARTIAL_UPDATED old=%s new=%s", old_partial, state.partial_malo_digits or state.corrected_market_location_number or "")
        db.add(state); db.commit(); db.refresh(state)

    async def _llm_or_policy(self, call, state, turns, transcript, language, intent, extra_instruction: str | None = None):
        directive = self._directive(call, state, transcript, intent, extra_instruction)
        logger.warning("NOMOS_AGENT_DIRECTIVE directive_type=%s", directive.get("directive_type"))
        key = self.settings.get('openai_api_key')
        if not key:
            logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=missing_openai_api_key")
            return self._policy(call, state, transcript, intent)
        payload = {"response_schema": {"spoken_reply": "string", "phase": "opening | waiting_for_operator_identity | waiting_for_case_result | collecting_malo_number | confirming_malo_number | waiting_for_next_step | operator_checking | closing | completed", "extracted_updates": {field: "string|null" for field in UPDATE_FIELDS}, "should_speak": True, "should_end_call": False, "reason": "short explanation"}, "conversation_directive": directive, "case_details": {"address": call.case.customer_address, "market_location_number": call.case.market_location_number, "problem": call.case.problem_description, "required_outcome": call.case.required_outcome}, "current_call_state": _asdict_state(state), "last_10_transcript_turns": [{"role": t.speaker, "text": t.text, "language": t.language, "source": t.source, "confidence": t.confidence} for t in turns], "latest_operator_transcript": transcript, "operator_intent": intent, "instruction": "Return only valid JSON. Follow memory and priority rules: do not ask for known fields; acknowledge frustration; one short human question max."}
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {key}'}, json={'model': self.settings.get('openai_model', 'gpt-4.1-mini'), 'temperature': float(self.settings.get('openai_temperature', '0.2') or 0.2), 'messages': [{'role': 'system', 'content': LIVE_VOICE_SYSTEM_PROMPT}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}], 'response_format': {'type': 'json_object'}})
                r.raise_for_status(); result = json.loads(r.json()['choices'][0]['message']['content'])
                if not self._valid_llm_result(result):
                    logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=invalid_llm_json"); return self._policy(call, state, transcript, intent)
                logger.warning("NOMOS_LLM_SPOKEN_REPLY text=%s", result.get("spoken_reply") or ""); return result
        except Exception:
            logger.exception("NOMOS_AGENT_LLM_FAILED using_policy_fallback=true"); logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=openai_failed"); return self._policy(call, state, transcript, intent)

    def _valid_llm_result(self, result: Any) -> bool:
        return isinstance(result, dict) and RESPONSE_FIELDS <= set(result) and result.get("phase") in ALLOWED_PHASES and isinstance(result.get("extracted_updates"), dict)

    async def _anti_loop(self, db, call, state, turns, transcript, language, result, intent):
        reply = result.get("spoken_reply") or ""; previous = [t.text for t in turns if t.speaker == "agent"][-3:]
        repeated_intent = state.waiting_for_field and state.waiting_for_field in {"registration_status", "case_result"} and _state_attr(state, "registration_status")
        if repeated_intent or any(similar(reply, p) for p in previous):
            logger.warning("NOMOS_AGENT_BLOCKED_REPEATED_INTENT field=%s", state.waiting_for_field)
            regen = await self._llm_or_policy(call, state, turns, transcript, language, intent, "Rewrite naturally. Do not ask a known field or repeat prior intent.")
            if not any(similar(regen.get("spoken_reply") or "", p) for p in previous): return regen
            return self._policy(call, state, transcript, intent, force_alternative=True)
        return result

    async def _style_adapter(self, call, state, turns, transcript, language, result, intent):
        reply = result.get("spoken_reply") or ""
        bad = len(re.split(r"[.!?]+", reply)) > 3 or "current result for this clearing case" in reply.lower() or ("expresses_frustration" in intent["intents"] and not re.search(r"sorry|right|apolog", reply.lower()))
        if _state_attr(state, "registration_status") and re.search(r"status|current result|still open", reply.lower()) and not state.corrected_market_location_number:
            bad = True
        if bad:
            logger.warning("NOMOS_AGENT_REPLY_REWRITTEN_FOR_STYLE")
            logger.warning("NOMOS_STYLE_REWRITE_APPLIED")
            result = self._policy(call, state, transcript, intent, force_alternative=True)
        return result

    def _directive(self, call, state, transcript: str, intent: dict[str, Any], extra_instruction: str | None = None) -> dict[str, Any]:
        directive_type, phase, missing, facts = self._next_step(state, intent)
        examples = [
            {"operator": "What are you talking about?", "good_agent": "Sorry, let me clarify. I’m calling about the registration for Musterstraße 12 in Mainz-Kastel. We need to confirm the correct market location number for that address."},
            {"operator": "I already told you. It is in progress.", "good_agent": "You're right, thank you. I have the status as in progress. I’m only missing the corrected market location number."},
            {"operator": "You're fired.", "good_agent": "I’m sorry, I know this is annoying. Let me keep it simple: I have the status as in progress, and I just need the corrected market location number."},
            {"operator": "Five one two.", "good_agent": "Got it, I have five one two so far. Please continue."},
            {"operator": "Eight zero zero four nine one two three.", "good_agent": "Thank you. I have five one two eight zero zero four nine one two three. Is that correct?"},
            {"operator": "Yes, correct. Resend it.", "good_agent": "Understood. I’ve noted the corrected number and that Nomos should resend the registration. Thank you for your help."},
        ]
        return {"directive_type": directive_type, "phase": phase, "missing_fields": missing, "known_fields": {"address": call.case.customer_address, "original_market_location_number": call.case.market_location_number, **{k: v for k, v in _asdict_state(state).items() if v}}, "latest_operator_message": transcript, "operator_intent": intent, "facts_to_confirm": facts, "priority_order": ["clarify case if confused", "corrected_market_location_number", "confirmation of number", "next_action", "reference_number", "close"], "do_not_say": ["current result for this clearing case", "Do not sound like an IVR.", "Do not ask for fields already captured."], "style_examples": examples, "notes_for_llm": "Use at most two short sentences. Acknowledge frustration/confusion. Ask only for the highest-priority missing item." + (f" Extra instruction: {extra_instruction}" if extra_instruction else ""), "tone": "natural, concise, professional", "extracted_updates": _empty_updates()}

    def _next_step(self, state, intent):
        intents = set(intent["intents"]); facts=[]
        if "asks_to_wait" in intents: return "acknowledge_wait", state.phase, [], facts
        if intents & {"asks_clarification", "asks_human_like_clarification"}: return "clarify_case", "collecting_malo_number", ["corrected_market_location_number"], facts
        if state.corrected_market_location_number:
            facts.append(f"corrected_market_location_number={state.corrected_market_location_number}")
            if state.waiting_for_field == "malo_confirmation" and "confirms_yes" not in intents:
                return "confirm_market_location_number", "confirming_malo_number", ["malo_confirmation"], facts
            if not state.next_action: return "ask_next_action", "waiting_for_next_step", ["next_action"], facts
            return "close_safely", "completed", [], facts
        digits = state.partial_malo_digits or ""
        if digits: return "collect_partial_malo", "collecting_malo_number", ["remaining_market_location_digits"], [f"partial_market_location_digits={digits}"]
        return "collect_market_location_number", "collecting_malo_number", ["corrected_market_location_number"], facts

    def _policy(self, call, state, transcript: str, intent: dict[str, Any] | None = None, force_alternative: bool = False):
        intent = intent or self._classify_intent(transcript, state); intents = set(intent["intents"]); updates = _empty_updates()
        if "asks_to_wait" in intents: return {"spoken_reply": "Of course, I’ll wait.", "phase": state.phase, "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "operator asked to wait"}
        if intents & {"asks_clarification", "asks_human_like_clarification"}: return {"spoken_reply": f"Sorry, let me clarify. I’m calling about the registration for {call.case.customer_address}. Can you see the correct market location number for that address?", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "clarification requested"}
        prefix = "I’m sorry, you’re right. " if "expresses_frustration" in intents else ""
        if state.corrected_market_location_number and ("confirms_yes" in intents or re.search(r"resend|send", (transcript or "").lower())):
            updates["corrected_market_location_number"] = state.corrected_market_location_number
            if re.search(r"resend|send|erneut", (transcript or "").lower()):
                updates["next_action"] = "resend_registration"; return {"spoken_reply": "Understood. I’ve noted the corrected number and that Nomos should resend the registration. Thank you for your help.", "phase": "completed", "extracted_updates": updates, "should_speak": True, "should_end_call": True, "reason": "confirmed number and next action"}
            return {"spoken_reply": "Thank you. Can Nomos resend the registration with the corrected number?", "phase": "waiting_for_next_step", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "number confirmed"}
        if state.corrected_market_location_number:
            updates["corrected_market_location_number"] = state.corrected_market_location_number
            return {"spoken_reply": f"Thank you. I have {digit_words(state.corrected_market_location_number)}. Is that correct?", "phase": "confirming_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "full MaLo detected"}
        digits = state.partial_malo_digits or intent.get("digits") or ""
        if digits:
            if len(digits) >= 10:
                return {"spoken_reply": f"I have {digit_words(digits)} so far. I may have missed one digit. Could you repeat the full market location number once slowly?", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "suspicious partial MaLo"}
            return {"spoken_reply": f"Got it, I have {digit_words(digits)} so far. Please continue.", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "partial MaLo detected"}
        status = _state_attr(state, "registration_status")
        if status:
            return {"spoken_reply": f"{prefix}I have the status as {status.replace('_', ' ')}. I’m only missing the corrected market location number.", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "ask highest priority missing field"}
        return {"spoken_reply": "Is the registration still in progress, rejected, or ready to resend?" if force_alternative else "Can you see the correct market location number for that address?", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "deterministic fallback"}

    async def extract(self, case, transcripts, events):
        text = ' '.join(t.text for t in transcripts)
        return {"outcome": "unclear", "root_cause": text[:500] or None, "market_location_number": case.market_location_number, "corrected_market_location_number": None, "meter_number": case.meter_number, "meter_status": "unknown", "reference_number": None, "registration_status": "unknown", "next_action": "none", "plain_language_note": "MVP extraction generated from saved transcript.", "confidence": 0.5}

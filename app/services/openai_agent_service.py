import json
import logging
import re
from typing import Any

import httpx

from app.agents.clearing_agent import ALLOWED_PHASES, LIVE_VOICE_SYSTEM_PROMPT, digit_words, normalize_spoken_digits, similar
from app.models import Call, CallState, CallTranscript

logger = logging.getLogger(__name__)

RESPONSE_FIELDS = {"spoken_reply", "phase", "extracted_updates", "should_speak", "should_end_call", "reason"}
UPDATE_FIELDS = ["corrected_market_location_number", "meter_status", "reference_number", "next_action"]
VALID_MODES = {"llm_led", "deterministic_safe"}


def _asdict_state(state: CallState) -> dict[str, Any]:
    return {k: getattr(state, k, None) for k in [
        "phase", "language", "known_operator_name", "known_market_location_number", "corrected_market_location_number",
        "partial_malo_digits", "meter_status", "reference_number", "next_action", "last_agent_question", "waiting_for_field",
    ]}


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
        self._update_state_from_digits(db, state, transcript)

        mode = self.settings.get("conversation_mode", "llm_led") or "llm_led"
        if mode not in VALID_MODES:
            mode = "llm_led"
        logger.warning("NOMOS_AGENT_MODE mode=%s", mode)
        logger.warning("NOMOS_AGENT_INPUT_STATE phase=%s", state.phase)
        logger.warning("NOMOS_AGENT_INPUT_HISTORY_TURNS count=%s", len(turns))

        if mode == "deterministic_safe":
            result = self._policy(call, state, transcript)
        else:
            result = await self._llm_or_policy(call, state, turns, transcript, language)
            result = await self._anti_loop(db, call, state, turns, transcript, language, result)

        old = state.phase
        new = result.get("phase") if result.get("phase") in ALLOWED_PHASES else old
        updates = result.get("extracted_updates") or {}
        for field in UPDATE_FIELDS:
            if updates.get(field):
                setattr(state, field, updates[field])
        state.phase = new
        state.last_agent_question = result.get("spoken_reply") or state.last_agent_question
        if old != new:
            logger.warning("NOMOS_CALL_PHASE_CHANGED old=%s new=%s", old, new)
        logger.warning("NOMOS_AGENT_OUTPUT phase=%s should_speak=%s", new, result.get("should_speak"))
        logger.warning("NOMOS_AGENT_EXTRACTED_UPDATES %s", updates)
        db.add(state); db.commit(); db.refresh(state)
        return result

    def _update_state_from_digits(self, db, state, transcript: str) -> None:
        digits = normalize_spoken_digits(transcript)
        if 2 <= len(digits) < 11:
            state.partial_malo_digits = (state.partial_malo_digits or "") + digits
            state.phase = "collecting_malo_number"
            db.add(state); db.commit(); db.refresh(state)
        elif len(digits) >= 11:
            state.corrected_market_location_number = digits[:11]
            state.partial_malo_digits = None
            state.phase = "confirming_malo_number"
            state.waiting_for_field = "malo_confirmation"
            db.add(state); db.commit(); db.refresh(state)

    async def _llm_or_policy(self, call, state, turns, transcript, language, extra_instruction: str | None = None):
        directive = self._directive(call, state, transcript, extra_instruction)
        logger.warning("NOMOS_AGENT_DIRECTIVE directive_type=%s", directive.get("directive_type"))
        key = self.settings.get('openai_api_key')
        if not key:
            logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=missing_openai_api_key")
            return self._policy(call, state, transcript)
        payload = {
            "response_schema": {"spoken_reply": "string", "phase": "opening | waiting_for_operator_identity | waiting_for_case_result | collecting_malo_number | confirming_malo_number | waiting_for_next_step | closing | completed", "extracted_updates": {field: "string|null" for field in UPDATE_FIELDS}, "should_speak": True, "should_end_call": False, "reason": "short explanation"},
            "conversation_directive": directive,
            "case_details": {"address": call.case.customer_address, "market_location_number": call.case.market_location_number, "problem": call.case.problem_description, "required_outcome": call.case.required_outcome},
            "current_call_state": _asdict_state(state),
            "last_10_transcript_turns": [{"role": t.speaker, "text": t.text, "language": t.language, "source": t.source, "confidence": t.confidence} for t in turns],
            "latest_operator_transcript": transcript,
            "instruction": "Return only valid JSON in the requested schema. The deterministic directive controls structure; you write only the natural spoken wording and any extracted updates.",
        }
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {key}'}, json={
                    'model': self.settings.get('openai_model', 'gpt-4.1-mini'), 'temperature': float(self.settings.get('openai_temperature', '0.2') or 0.2),
                    'messages': [{'role': 'system', 'content': LIVE_VOICE_SYSTEM_PROMPT}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                    'response_format': {'type': 'json_object'},
                })
                r.raise_for_status()
                result = json.loads(r.json()['choices'][0]['message']['content'])
                if not self._valid_llm_result(result):
                    logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=invalid_llm_json")
                    return self._policy(call, state, transcript)
                logger.warning("NOMOS_LLM_SPOKEN_REPLY text=%s", result.get("spoken_reply") or "")
                return result
        except Exception:
            logger.exception("NOMOS_AGENT_LLM_FAILED using_policy_fallback=true")
            logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=openai_failed")
            return self._policy(call, state, transcript)

    def _valid_llm_result(self, result: Any) -> bool:
        return isinstance(result, dict) and RESPONSE_FIELDS <= set(result) and result.get("phase") in ALLOWED_PHASES and isinstance(result.get("extracted_updates"), dict)

    async def _anti_loop(self, db, call, state, turns, transcript, language, result):
        reply = result.get("spoken_reply") or ""
        previous = [t.text for t in turns if t.speaker == "agent"][-3:]
        if any(similar(reply, p) for p in previous):
            logger.warning("NOMOS_AGENT_STYLE_RETRY reason=repeat_detected")
            regen = await self._llm_or_policy(call, state, turns, transcript, language, "Your previous reply was too similar. Rephrase naturally and continue from the operator’s latest message. Do not repeat the case description unless necessary.")
            if not any(similar(regen.get("spoken_reply") or "", p) for p in previous):
                return regen
            logger.warning("NOMOS_DETERMINISTIC_FALLBACK_USED reason=repeat_detected")
            return self._policy(call, state, transcript, force_alternative=True)
        return result

    def _directive(self, call, state, transcript: str, extra_instruction: str | None = None) -> dict[str, Any]:
        text = (transcript or "").lower()
        updates = _empty_updates()
        digits = state.corrected_market_location_number or state.partial_malo_digits or normalize_spoken_digits(transcript)
        directive_type = "ask_for_case_result"
        phase = "waiting_for_case_result"
        missing = ["corrected_market_location_number", "meter_status", "next_action"]
        facts = []
        allowed = ["ask_for_case_result", "collect_market_location_number", "confirm_market_location_number", "ask_next_action", "close_safely"]
        notes = "Acknowledge the latest operator message, then ask one concise next question. Never reuse the exact previous agent sentence. If the same phase repeats, rephrase naturally. If irrelevant speech appears, gently steer back without repeating the full case description."
        if re.search(r"hold on|please hold|wait|moment|warte|warten|augenblick", text):
            directive_type = "acknowledge_wait"; phase = state.phase; missing = []; allowed = ["brief_wait_acknowledgement"]; notes = "Operator asked you to wait. Reply briefly like a human and do not ask a new question."
        elif state.corrected_market_location_number and re.search(r"yes|correct|that is correct|resend|ja|richtig|stimmt|erneut|senden", text):
            if re.search(r"resend|erneut|senden|can resend|you can", text):
                directive_type = "close_after_next_action"; phase = "completed"; updates["next_action"] = "resend_registration"; missing = []
                notes = "Confirm the corrected number and next action have been noted, thank them, and close safely."
            else:
                directive_type = "ask_for_next_action"; phase = "waiting_for_next_step"; missing = ["next_action"]
                notes = "They confirmed the number. Ask whether Nomos should resend the registration with that corrected number."
        elif state.corrected_market_location_number:
            directive_type = "confirm_full_malo"; phase = "confirming_malo_number"; updates["corrected_market_location_number"] = state.corrected_market_location_number; missing = ["malo_confirmation"]
            facts = [f"corrected_market_location_number={state.corrected_market_location_number}"]
            notes = f"Repeat the market-location number digit by digit as: {digit_words(state.corrected_market_location_number)}. Ask if it is correct."
        elif digits and len(digits) < 11:
            directive_type = "collect_partial_malo"; phase = "collecting_malo_number"; missing = ["remaining_market_location_digits"]
            facts = [f"partial_market_location_digits={digits}"]
            notes = f"Say you heard the start as {digit_words(digits)} and ask naturally for the remaining digits."
        return {"directive_type": directive_type, "conversation_goal": directive_type, "phase": phase, "missing_fields": missing, "known_fields": {"address": call.case.customer_address, "original_market_location_number": call.case.market_location_number, **{k: v for k, v in _asdict_state(state).items() if v}}, "latest_operator_message": transcript, "facts_to_confirm": facts, "allowed_actions": allowed, "do_not_say": ["Do not sound like an IVR.", "Do not repeat the full case description unless needed.", "Do not invent facts."], "notes_for_llm": notes + (f" Extra instruction: {extra_instruction}" if extra_instruction else ""), "tone": "natural, concise, professional", "extracted_updates": updates}

    def _policy(self, call, state, transcript: str, force_alternative: bool = False):
        text = (transcript or "").lower()
        updates = _empty_updates()
        if re.search(r"hold on|please hold|wait|moment|warte|warten|augenblick", text):
            return {"spoken_reply": "Of course, I’ll wait.", "phase": state.phase, "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "operator asked to wait"}
        digits = state.corrected_market_location_number or state.partial_malo_digits or normalize_spoken_digits(transcript)
        if state.corrected_market_location_number and re.search(r"yes|correct|that is correct|resend|ja|richtig|stimmt|erneut|senden", text):
            if re.search(r"resend|erneut|senden|can resend|you can", text):
                updates["next_action"] = "resend_registration"
                return {"spoken_reply": "Understood. I’ve noted that Nomos should resend the registration with the corrected number. Thank you, goodbye.", "phase": "completed", "extracted_updates": updates, "should_speak": True, "should_end_call": True, "reason": "confirmed number and next action"}
            return {"spoken_reply": "Thank you. Should Nomos resend the registration with this corrected number?", "phase": "waiting_for_next_step", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "number confirmed"}
        if state.corrected_market_location_number:
            updates["corrected_market_location_number"] = state.corrected_market_location_number
            return {"spoken_reply": f"Thank you. I’ll repeat that back digit by digit: {digit_words(state.corrected_market_location_number)}. Is that correct?", "phase": "confirming_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "full MaLo detected"}
        if digits and len(digits) < 11:
            return {"spoken_reply": f"I heard the start as {digit_words(digits)}. Please continue with the remaining digits.", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "partial MaLo detected"}
        return {"spoken_reply": "Thanks. Could you tell me the current result for this clearing case?" if force_alternative else "Thank you. Could you please tell me the current result for this clearing case?", "phase": "waiting_for_case_result", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "deterministic fallback"}

    async def extract(self, case, transcripts, events):
        text = ' '.join(t.text for t in transcripts)
        return {"outcome": "unclear", "root_cause": text[:500] or None, "market_location_number": case.market_location_number, "corrected_market_location_number": None, "meter_number": case.meter_number, "meter_status": "unknown", "reference_number": None, "registration_status": "unknown", "next_action": "none", "plain_language_note": "MVP extraction generated from saved transcript.", "confidence": 0.5}

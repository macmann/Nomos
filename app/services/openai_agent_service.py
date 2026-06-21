import json
import logging
import re
from typing import Any

import httpx

from app.agents.clearing_agent import ALLOWED_PHASES, LIVE_VOICE_SYSTEM_PROMPT, digit_words, normalize_spoken_digits, similar
from app.models import Call, CallState, CallTranscript

logger = logging.getLogger(__name__)


def _asdict_state(state: CallState) -> dict[str, Any]:
    return {k: getattr(state, k, None) for k in [
        "phase", "language", "known_operator_name", "known_market_location_number", "corrected_market_location_number",
        "partial_malo_digits", "meter_status", "reference_number", "next_action", "last_agent_question", "waiting_for_field",
    ]}


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

        logger.warning("NOMOS_AGENT_INPUT_STATE phase=%s", state.phase)
        logger.warning("NOMOS_AGENT_INPUT_HISTORY_TURNS count=%s", len(turns))
        result = await self._llm_or_policy(call, state, turns, transcript, language)
        result = await self._anti_loop(db, call, state, turns, transcript, language, result)

        old = state.phase
        new = result.get("phase") if result.get("phase") in ALLOWED_PHASES else old
        updates = result.get("extracted_updates") or {}
        for field in ["corrected_market_location_number", "meter_status", "reference_number", "next_action"]:
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

    async def _llm_or_policy(self, call, state, turns, transcript, language, extra_instruction: str | None = None):
        key = self.settings.get('openai_api_key')
        if not key:
            return self._policy(call, state, transcript)
        payload = {
            "system_prompt": LIVE_VOICE_SYSTEM_PROMPT,
            "case_details": {"address": call.case.customer_address, "market_location_number": call.case.market_location_number, "problem": call.case.problem_description, "required_outcome": call.case.required_outcome},
            "current_call_state": _asdict_state(state),
            "last_10_transcript_turns": [{"role": t.speaker, "text": t.text, "language": t.language, "source": t.source, "confidence": t.confidence} for t in turns],
            "latest_operator_transcript": transcript,
            "known_extracted_fields": _asdict_state(state),
            "current_phase": state.phase,
            "instruction": "Return only JSON with spoken_reply, phase, extracted_updates, should_speak, should_end_call, reason. " + (extra_instruction or ""),
        }
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post('https://api.openai.com/v1/chat/completions', headers={'Authorization': f'Bearer {key}'}, json={
                    'model': self.settings.get('openai_model', 'gpt-4.1-mini'), 'temperature': float(self.settings.get('openai_temperature', '0.2') or 0.2),
                    'messages': [{'role': 'system', 'content': LIVE_VOICE_SYSTEM_PROMPT}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                    'response_format': {'type': 'json_object'},
                })
                r.raise_for_status()
                return json.loads(r.json()['choices'][0]['message']['content'])
        except Exception:
            logger.exception("NOMOS_AGENT_LLM_FAILED using_policy_fallback=true")
            return self._policy(call, state, transcript)

    async def _anti_loop(self, db, call, state, turns, transcript, language, result):
        reply = result.get("spoken_reply") or ""
        previous = [t.text for t in turns if t.speaker == "agent"][-3:]
        if any(similar(reply, p) for p in previous):
            logger.warning("NOMOS_AGENT_REPEAT_DETECTED")
            regen = await self._llm_or_policy(call, state, turns, transcript, language, "Do not repeat the previous question. Continue from the latest operator answer.")
            if not any(similar(regen.get("spoken_reply") or "", p) for p in previous):
                logger.warning("NOMOS_AGENT_REGENERATED")
                return regen
            return self._policy(call, state, transcript, force_alternative=True)
        return result

    def _policy(self, call, state, transcript: str, force_alternative: bool = False):
        text = (transcript or "").lower()
        updates = {"corrected_market_location_number": None, "meter_status": None, "reference_number": None, "next_action": None}
        if re.search(r"hold on|please hold|wait|moment|warte|warten|augenblick", text):
            return {"spoken_reply": "Of course, I will hold." if "hold" in text or "wait" in text else "No problem, I am listening.", "phase": state.phase, "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "operator asked to wait"}
        digits = state.corrected_market_location_number or state.partial_malo_digits or normalize_spoken_digits(transcript)
        if state.corrected_market_location_number and re.search(r"yes|correct|that is correct|resend|ja|richtig|stimmt|erneut|senden", text):
            if re.search(r"resend|erneut|senden|can resend|you can", text):
                updates["next_action"] = "resend_registration"
                return {"spoken_reply": "Understood. I have noted the corrected market location number and that Nomos should resend the registration. Thank you. Goodbye.", "phase": "completed", "extracted_updates": updates, "should_speak": True, "should_end_call": True, "reason": "confirmed number and next action"}
            return {"spoken_reply": "Thank you. Can Nomos resend the registration with this corrected market location number?", "phase": "waiting_for_next_step", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "number confirmed"}
        if state.corrected_market_location_number:
            updates["corrected_market_location_number"] = state.corrected_market_location_number
            return {"spoken_reply": f"Thank you. I will repeat that digit by digit: {digit_words(state.corrected_market_location_number)}. Is that correct?", "phase": "confirming_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "full MaLo detected"}
        if digits and len(digits) < 11:
            return {"spoken_reply": f"I heard the start of the market location number as {digit_words(digits)}. Please continue with the remaining digits.", "phase": "collecting_malo_number", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "partial MaLo detected"}
        intro = f"Thank you. This is regarding {call.case.customer_address}. " if state.phase == "opening" and not force_alternative else "Thank you. "
        return {"spoken_reply": intro + "Could you please tell me the current result for this clearing case?", "phase": "waiting_for_case_result", "extracted_updates": updates, "should_speak": True, "should_end_call": False, "reason": "default phase prompt"}

    async def extract(self, case, transcripts, events):
        text = ' '.join(t.text for t in transcripts)
        return {"outcome": "unclear", "root_cause": text[:500] or None, "market_location_number": case.market_location_number, "corrected_market_location_number": None, "meter_number": case.meter_number, "meter_status": "unknown", "reference_number": None, "registration_status": "unknown", "next_action": "none", "plain_language_note": "MVP extraction generated from saved transcript.", "confidence": 0.5}

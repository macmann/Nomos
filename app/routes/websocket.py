import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.config import get_settings
from app.database import SessionLocal
from app.models import Call, CallEvent, CallTranscript
from app.services.elevenlabs_service import ElevenLabsService
from app.services.language_service import detect_language, dtmf_for_ivr, is_ivr
from app.services.openai_agent_service import OpenAIAgentService
from app.settings_service import SettingsService

logger = logging.getLogger(__name__)
router = APIRouter()
ACTIVE_TWILIO_SESSIONS: dict[int, dict[str, Any]] = {}
GREETING = "Guten Tag, ich bin ein KI-Assistent von Nomos. Dies ist ein kurzer Verbindungstest."


def _bool_setting(value: Any, default: bool = False) -> bool:
    if value is None: return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_raw(raw: str, limit: int = 1000) -> str:
    return raw if len(raw) <= limit else f"{raw[:limit]}...[truncated {len(raw)-limit} chars]"


def _write_event(call_id: int, event_type: str, payload: dict | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(CallEvent(call_id=call_id, event_type=event_type, event_payload=payload or {})); db.commit()
    except Exception:
        db.rollback(); logger.exception("NOMOS_WS_ERROR failed_to_persist call_id=%s event_type=%s", call_id, event_type)
    finally:
        db.close()


def _update_call(call_id: int, **updates: Any) -> None:
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if call:
            for k, v in updates.items(): setattr(call, k, v)
            db.commit()
    except Exception:
        db.rollback(); logger.exception("NOMOS_WS_ERROR failed_to_update_call call_id=%s", call_id)
    finally:
        db.close()


def _settings_snapshot() -> dict[str, Any]:
    db = SessionLocal()
    try:
        ss = SettingsService(db, get_settings().app_encryption_key)
        safe = _bool_setting(ss.get("voice_safe_mode", "true"), True)
        return {
            "voice_safe_mode": safe,
            "allow_greeting_in_safe_mode": _bool_setting(ss.get("allow_greeting_in_safe_mode", "false"), False),
            "greeting_on_start_enabled": _bool_setting(ss.get("greeting_on_start_enabled", "true"), True),
            "text_debug_mode": _bool_setting(ss.get("text_debug_mode", "false"), False),
            "stt_enabled": False if safe else _bool_setting(ss.get("stt_enabled", "false")),
            "agent_enabled": False if safe else _bool_setting(ss.get("agent_enabled", "false")),
            "tts_enabled": False if safe else _bool_setting(ss.get("tts_enabled", "false")),
            "twilio_max_call_duration": int(ss.get("twilio_max_call_duration", "600") or 600),
        }
    except Exception:
        logger.exception("NOMOS_WS_ERROR failed_to_load_settings")
        return {"voice_safe_mode": True, "allow_greeting_in_safe_mode": False, "greeting_on_start_enabled": True, "text_debug_mode": False, "stt_enabled": False, "agent_enabled": False, "tts_enabled": False, "twilio_max_call_duration": 600}
    finally:
        db.close()


async def _send_twilio_audio(ws: WebSocket, call_id: int, stream_sid: str, audio: bytes, chunk_size: int = 160) -> None:
    chunks = [audio[i:i+chunk_size] for i in range(0, len(audio), chunk_size) if audio[i:i+chunk_size]]
    for chunk in chunks:
        await ws.send_json({"event": "media", "streamSid": stream_sid, "media": {"payload": base64.b64encode(chunk).decode("ascii")}})
        await asyncio.sleep(0.02 if chunk_size == 160 else 0.04)
    logger.warning("NOMOS_TWILIO_AUDIO_SENT bytes=%s chunks=%s", len(audio), len(chunks))
    _write_event(call_id, "twilio_audio_sent", {"streamSid": stream_sid, "bytes": len(audio), "chunks": len(chunks)})


async def send_test_greeting(call_id: int) -> bool:
    session = ACTIVE_TWILIO_SESSIONS.get(call_id)
    if not session or not session.get("stream_sid"):
        return False
    await _speak_text(session["ws"], call_id, session["stream_sid"], GREETING, "de-DE")
    return True


async def _speak_text(ws: WebSocket, call_id: int, stream_sid: str, text: str, language: str = "de-DE") -> None:
    logger.warning("NOMOS_TTS_START text_len=%s", len(text)); _write_event(call_id, "tts_started", {"text_len": len(text)})
    db = SessionLocal()
    try:
        audio = await ElevenLabsService(SettingsService(db, get_settings().app_encryption_key)).text_to_twilio_audio(text, language)
    finally:
        db.close()
    logger.warning("NOMOS_TTS_BYTES bytes=%s", len(audio)); _write_event(call_id, "tts_completed", {"bytes": len(audio)})
    await _send_twilio_audio(ws, call_id, stream_sid, audio)


async def _process_buffer(ws: WebSocket, call_id: int, stream_sid: str | None, audio: bytes) -> None:
    settings = _settings_snapshot()
    if settings["voice_safe_mode"]:
        logger.warning("NOMOS_STT_DISABLED call_id=%s voice_safe_mode=true", call_id); return
    if not settings["stt_enabled"]:
        logger.warning("NOMOS_STT_DISABLED call_id=%s stt_enabled=false", call_id); return
    logger.warning("NOMOS_STT_START bytes=%s", len(audio)); _write_event(call_id, "stt_started", {"bytes": len(audio)})
    transcript_text = None; language = None
    try:
        db = SessionLocal()
        try:
            ss = SettingsService(db, get_settings().app_encryption_key)
            transcript_text = await ElevenLabsService(ss).transcribe_chunk(audio)
            logger.warning("NOMOS_STT_RESULT text=%s", transcript_text or "")
            _write_event(call_id, "stt_completed", {"text": transcript_text or ""})
            call = db.get(Call, call_id)
            if transcript_text and call:
                language = detect_language(transcript_text) if call.case.language_mode == "auto" else call.active_language or call.case.preferred_language
                call.active_language = language
                speaker = "ivr" if is_ivr(transcript_text) else "operator"
                db.add(CallTranscript(call_id=call_id, speaker=speaker, text=transcript_text, language=language, confidence=0.8)); db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("NOMOS_WS_ERROR stage=stt call_id=%s", call_id); _write_event(call_id, "websocket_error", {"stage": "stt"}); return
    if not transcript_text:
        logger.warning("NOMOS_STT_RESULT_EMPTY call_id=%s", call_id); return
    digit = dtmf_for_ivr(transcript_text)
    if digit and stream_sid:
        await ws.send_json({"event": "dtmf", "streamSid": stream_sid, "dtmf": {"digits": digit}}); _write_event(call_id, "dtmf_sent", {"digit": digit}); return
    if not settings["agent_enabled"]: return
    logger.warning("NOMOS_AGENT_START"); _write_event(call_id, "agent_started", {"text": transcript_text})
    response = None
    try:
        db = SessionLocal()
        try:
            call = db.get(Call, call_id)
            if call:
                ss = SettingsService(db, get_settings().app_encryption_key)
                response = await OpenAIAgentService(ss).respond(call.case, transcript_text, language or call.active_language or "de-DE")
                db.add(CallTranscript(call_id=call_id, speaker="agent", text=response, language=language or call.active_language)); db.commit()
        finally: db.close()
    except Exception:
        logger.exception("NOMOS_WS_ERROR stage=agent call_id=%s", call_id); _write_event(call_id, "websocket_error", {"stage": "agent"}); return
    logger.warning("NOMOS_AGENT_RESPONSE text=%s", response or ""); _write_event(call_id, "agent_completed", {"text": response or ""})
    if response and settings["tts_enabled"] and not settings["text_debug_mode"] and stream_sid:
        await _speak_text(ws, call_id, stream_sid, response, language or "de-DE")


@router.websocket("/ws/twilio-media/{call_id}")
async def media(ws: WebSocket, call_id: int):
    await ws.accept(); started = time.time(); stream_sid = None; buffer = bytearray(); last_flush = time.time(); disconnected = False
    settings = _settings_snapshot()
    ACTIVE_TWILIO_SESSIONS[call_id] = {"ws": ws, "stream_sid": None}
    logger.warning("NOMOS_WS_CONNECTED call_id=%s", call_id); _write_event(call_id, "websocket_connected", {"voice_safe_mode": settings["voice_safe_mode"]})
    try:
        while True:
            try: raw = await ws.receive_text()
            except WebSocketDisconnect:
                disconnected=True; logger.warning("NOMOS_WS_STOP call_id=%s disconnected=true", call_id); _write_event(call_id, "websocket_disconnected", {}); break
            try: msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.exception("NOMOS_WS_ERROR call_id=%s stage=json_parse raw=%s", call_id, _safe_raw(raw)); _write_event(call_id, "websocket_error", {"stage":"json_parse","raw":_safe_raw(raw)}); continue
            event = msg.get("event"); logger.warning("NOMOS_WS_RAW event=%s", event)
            try:
                if event == "connected":
                    _write_event(call_id, "twilio_connected_received", {})
                elif event == "start":
                    start = msg.get("start") or {}; stream_sid = start.get("streamSid") or msg.get("streamSid"); call_sid = start.get("callSid")
                    ACTIVE_TWILIO_SESSIONS[call_id]["stream_sid"] = stream_sid
                    payload = {"streamSid": stream_sid, "callSid": call_sid, "accountSid": start.get("accountSid"), "customParameters": start.get("customParameters") or {}}
                    logger.warning("NOMOS_TWILIO_START streamSid=%s callSid=%s", stream_sid, call_sid); _write_event(call_id, "twilio_start_received", payload)
                    if call_sid: _update_call(call_id, twilio_call_sid=call_sid, status="in_progress", started_at=datetime.utcnow())
                    if stream_sid and settings["greeting_on_start_enabled"] and (not settings["voice_safe_mode"] or settings["allow_greeting_in_safe_mode"]):
                        await _speak_text(ws, call_id, stream_sid, GREETING, "de-DE")
                elif event == "media":
                    mp = msg.get("media") or {}; payload = mp.get("payload") or ""; chunk = mp.get("chunk"); timestamp = mp.get("timestamp")
                    audio = base64.b64decode(payload, validate=True) if payload else b""
                    logger.warning("NOMOS_TWILIO_MEDIA chunk=%s timestamp=%s bytes=%s", chunk, timestamp, len(audio)); _write_event(call_id, "twilio_media_received", {"chunk": chunk, "timestamp": timestamp, "bytes": len(audio), "streamSid": msg.get("streamSid") or stream_sid})
                    buffer.extend(audio); logger.warning("NOMOS_STT_BUFFER_BYTES bytes=%s", len(buffer))
                    if len(buffer) >= 12000 or (buffer and time.time() - last_flush >= 2.0):
                        flushed = bytes(buffer); buffer.clear(); last_flush = time.time(); logger.warning("NOMOS_STT_FLUSH bytes=%s", len(flushed)); await _process_buffer(ws, call_id, stream_sid, flushed)
                elif event == "mark":
                    _write_event(call_id, "twilio_mark_received", {"mark": msg.get("mark")})
                elif event == "dtmf":
                    _write_event(call_id, "twilio_dtmf_received", {"dtmf": msg.get("dtmf")})
                elif event == "stop":
                    logger.warning("NOMOS_WS_STOP call_id=%s", call_id); _write_event(call_id, "twilio_stop_received", {"streamSid": msg.get("streamSid") or stream_sid, "stop": msg.get("stop") or {}}); _update_call(call_id, status="completed", ended_at=datetime.utcnow()); break
                else:
                    _write_event(call_id, "unknown_twilio_event", {"event": event})
            except Exception:
                logger.exception("NOMOS_WS_ERROR call_id=%s stage=event_processing", call_id); _write_event(call_id, "websocket_error", {"stage":"event_processing", "event": event})
            if time.time() - started > settings["twilio_max_call_duration"]:
                _write_event(call_id, "twilio_stop_received", {"reason":"max_duration"}); break
    finally:
        ACTIVE_TWILIO_SESSIONS.pop(call_id, None)
        if not disconnected: _write_event(call_id, "websocket_disconnected", {})
        if ws.client_state != WebSocketState.DISCONNECTED:
            try: await ws.close()
            except Exception: logger.exception("NOMOS_WS_ERROR call_id=%s stage=close", call_id)

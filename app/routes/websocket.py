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


def _bool_setting(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_raw(raw: str, limit: int = 1000) -> str:
    if len(raw) <= limit:
        return raw
    return f"{raw[:limit]}...[truncated {len(raw) - limit} chars]"


def _write_event(call_id: int, event_type: str, payload: dict | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(CallEvent(call_id=call_id, event_type=event_type, event_payload=payload or {}))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist call event", extra={"call_id": call_id, "event_type": event_type})
    finally:
        db.close()


def _update_call(call_id: int, **updates: Any) -> None:
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if call:
            for key, value in updates.items():
                setattr(call, key, value)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to update call", extra={"call_id": call_id, "updates": list(updates)})
    finally:
        db.close()


def _settings_snapshot() -> dict[str, Any]:
    db = SessionLocal()
    try:
        ss = SettingsService(db, get_settings().app_encryption_key)
        voice_safe_mode = _bool_setting(ss.get("voice_safe_mode", "true"), True)
        return {
            "voice_safe_mode": voice_safe_mode,
            "stt_enabled": False if voice_safe_mode else _bool_setting(ss.get("stt_enabled", "false")),
            "agent_enabled": False if voice_safe_mode else _bool_setting(ss.get("agent_enabled", "false")),
            "tts_enabled": False if voice_safe_mode else _bool_setting(ss.get("tts_enabled", "false")),
            "twilio_max_call_duration": int(ss.get("twilio_max_call_duration", "600") or 600),
        }
    except Exception:
        logger.exception("Failed to load voice settings; falling back to safe mode")
        return {
            "voice_safe_mode": True,
            "stt_enabled": False,
            "agent_enabled": False,
            "tts_enabled": False,
            "twilio_max_call_duration": 600,
        }
    finally:
        db.close()


async def _process_optional_pipeline(ws: WebSocket, call_id: int, stream_sid: str | None, audio: bytes) -> None:
    settings = _settings_snapshot()
    if settings["voice_safe_mode"]:
        return

    transcript_text: str | None = None
    language: str | None = None
    call = None

    if settings["stt_enabled"]:
        try:
            db = SessionLocal()
            try:
                ss = SettingsService(db, get_settings().app_encryption_key)
                transcript_text = await ElevenLabsService(ss).transcribe_chunk(audio)
                call = db.get(Call, call_id)
                if transcript_text and call:
                    language = detect_language(transcript_text) if call.case.language_mode == "auto" else call.active_language or call.case.preferred_language
                    call.detected_language = language if call.case.language_mode == "auto" else call.detected_language
                    call.active_language = language
                    speaker = "ivr" if is_ivr(transcript_text) else "operator"
                    db.add(CallTranscript(call_id=call_id, speaker=speaker, text=transcript_text, language=language, confidence=0.8))
                    db.add(CallEvent(call_id=call_id, event_type="stt_final", event_payload={"text": transcript_text}))
                    db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("stt_error: STT failed", extra={"call_id": call_id})
            _write_event(call_id, "error", {"stage": "stt"})
            return

    if not transcript_text:
        return

    digit = dtmf_for_ivr(transcript_text)
    if digit:
        try:
            await ws.send_json({"event": "dtmf", "streamSid": stream_sid, "dtmf": {"digits": digit}})
            _write_event(call_id, "dtmf_sent", {"digit": digit})
        except Exception:
            logger.exception("twilio_audio_out_error: Failed to send DTMF to Twilio", extra={"call_id": call_id})
            _write_event(call_id, "error", {"stage": "twilio_audio_out"})
        return

    response: str | None = None
    if settings["agent_enabled"]:
        try:
            db = SessionLocal()
            try:
                call = db.get(Call, call_id)
                if call:
                    ss = SettingsService(db, get_settings().app_encryption_key)
                    response = await OpenAIAgentService(ss).respond(call.case, transcript_text, language or call.active_language or "de-DE")
                    db.add(CallTranscript(call_id=call_id, speaker="agent", text=response, language=language or call.active_language))
                    db.add(CallEvent(call_id=call_id, event_type="agent_response", event_payload={"text": response}))
                    db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("openai_error: OpenAI agent failed", extra={"call_id": call_id})
            _write_event(call_id, "error", {"stage": "openai"})
            return

    if not response or not settings["tts_enabled"]:
        return

    try:
        db = SessionLocal()
        try:
            ss = SettingsService(db, get_settings().app_encryption_key)
            tts_frames = await ElevenLabsService(ss).text_to_twilio_frames(response, language or "de-DE")
            for payload in tts_frames:
                try:
                    await ws.send_json({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}})
                except Exception:
                    logger.exception("twilio_audio_out_error: Failed to send audio to Twilio", extra={"call_id": call_id})
                    _write_event(call_id, "error", {"stage": "twilio_audio_out"})
                    break
        finally:
            db.close()
    except Exception:
        logger.exception("tts_error: TTS failed", extra={"call_id": call_id})
        _write_event(call_id, "error", {"stage": "tts"})


@router.websocket("/ws/twilio-media/{call_id}")
async def media(ws: WebSocket, call_id: int):
    await ws.accept()
    started = time.time()
    stream_sid: str | None = None
    disconnected = False
    logger.info("websocket_connected", extra={"call_id": call_id})
    settings = _settings_snapshot()
    _write_event(call_id, "websocket_connected", {"voice_safe_mode": settings["voice_safe_mode"]})

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                disconnected = True
                logger.info("websocket_disconnected", extra={"call_id": call_id})
                _write_event(call_id, "websocket_disconnected", {})
                break

            logger.info("raw_message_received", extra={"call_id": call_id, "raw_length": len(raw), "raw_message": _safe_raw(raw)})
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.exception("websocket_error", extra={"call_id": call_id, "stage": "json_parse", "raw_message": _safe_raw(raw)})
                _write_event(call_id, "websocket_error", {"stage": "json_parse", "raw_message": _safe_raw(raw)})
                continue

            try:
                event = msg.get("event")
                if event == "connected":
                    logger.info("twilio_connected_event", extra={"call_id": call_id})
                elif event == "start":
                    start = msg.get("start") or {}
                    stream_sid = start.get("streamSid") or msg.get("streamSid")
                    call_sid = start.get("callSid")
                    account_sid = start.get("accountSid")
                    custom_parameters = start.get("customParameters") or {}
                    payload = {"streamSid": stream_sid, "callSid": call_sid, "accountSid": account_sid, "customParameters": custom_parameters}
                    logger.info("twilio_start_event", extra={"call_id": call_id, **payload})
                    _write_event(call_id, "twilio_start_received", payload)
                    if call_sid:
                        db = SessionLocal()
                        try:
                            call = db.get(Call, call_id)
                            if call:
                                if not call.twilio_call_sid:
                                    call.twilio_call_sid = call_sid
                                call.status = "in_progress"
                                call.started_at = call.started_at or datetime.utcnow()
                                db.commit()
                        except Exception:
                            db.rollback()
                            logger.exception("Failed to update call from Twilio start", extra={"call_id": call_id})
                        finally:
                            db.close()
                elif event == "media":
                    media_payload = msg.get("media")
                    if not media_payload or "payload" not in media_payload:
                        logger.warning("twilio_media_event missing payload", extra={"call_id": call_id})
                        _write_event(call_id, "error", {"stage": "media_validate", "message": "missing media.payload"})
                        continue
                    payload = media_payload.get("payload") or ""
                    track = media_payload.get("track")
                    chunk = media_payload.get("chunk")
                    timestamp = media_payload.get("timestamp")
                    payload_length = len(payload)
                    logger.info("twilio_media_event", extra={"call_id": call_id, "streamSid": msg.get("streamSid") or stream_sid, "track": track, "chunk": chunk, "timestamp": timestamp, "payload_length": payload_length})
                    try:
                        audio = base64.b64decode(payload, validate=True)
                    except Exception:
                        logger.exception("websocket_error", extra={"call_id": call_id, "stage": "media_decode", "chunk": chunk})
                        _write_event(call_id, "error", {"stage": "media_decode", "chunk": chunk, "timestamp": timestamp, "track": track, "payload_length": payload_length})
                        continue
                    _write_event(call_id, "twilio_media_received", {"chunk": chunk, "timestamp": timestamp, "track": track, "payload_length": payload_length, "decoded_bytes_length": len(audio)})
                    await _process_optional_pipeline(ws, call_id, stream_sid, audio)
                elif event == "mark":
                    logger.info("twilio_mark_event", extra={"call_id": call_id, "mark": msg.get("mark")})
                elif event == "dtmf":
                    logger.info("twilio_dtmf_event", extra={"call_id": call_id, "dtmf": msg.get("dtmf")})
                elif event == "stop":
                    logger.info("twilio_stop_event", extra={"call_id": call_id, "stop": msg.get("stop")})
                    _write_event(call_id, "twilio_stop_received", {"streamSid": msg.get("streamSid") or stream_sid, "stop": msg.get("stop") or {}})
                    _update_call(call_id, status="completed", ended_at=datetime.utcnow())
                    break
                else:
                    logger.warning("unknown_twilio_event", extra={"call_id": call_id, "event": event})
                    _write_event(call_id, "unknown_twilio_event", {"event": event})
            except Exception:
                logger.exception("websocket_error", extra={"call_id": call_id, "stage": "event_processing"})
                _write_event(call_id, "websocket_error", {"stage": "event_processing"})
                continue

            if time.time() - started > settings["twilio_max_call_duration"]:
                logger.info("twilio_stop_event", extra={"call_id": call_id, "reason": "max_duration"})
                _write_event(call_id, "twilio_stop_received", {"reason": "max_duration"})
                _update_call(call_id, status="stopped", ended_at=datetime.utcnow())
                break
    except Exception:
        logger.exception("websocket_error", extra={"call_id": call_id, "stage": "receive_loop"})
        _write_event(call_id, "websocket_error", {"stage": "receive_loop"})
    finally:
        if not disconnected and ws.client_state != WebSocketState.DISCONNECTED:
            try:
                await ws.close()
            except Exception:
                logger.exception("websocket_error", extra={"call_id": call_id, "stage": "close"})

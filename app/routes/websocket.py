import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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
INVALID_TRANSCRIPTS = {
    "",
    "(heavy static)",
    "(static noise)",
    "(static hissing)",
    "(white noise)",
    "(traffic noise)",
    "(techno music)",
    "(clicking)",
    "(two beeps)",
    "(screeching noise)",
    "[noise]",
    "(noise)",
    "[silence]",
    "(silence)",
    "mm",
    "mm-hmm",
    "mm hmm",
}
MEANINGFUL_SHORT_TRANSCRIPTS = {"ja", "yes", "no", "ok", "nein"}
TWILIO_AUDIO_CHUNK_BYTES = 160
TWILIO_AUDIO_CHUNK_SECONDS = 0.02
MEDIA_EVENT_SAMPLE_CHUNKS = 50
MIN_STT_AUDIO_BYTES = 16000
DEFAULT_STT_FLUSH_BYTES = 24000
DEFAULT_STT_FLUSH_SECONDS = 3.0
DEFAULT_STT_AFTER_BOT_COOLDOWN_MS = 500


def _bool_setting(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _int_setting(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_setting(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_raw(raw: str, limit: int = 1000) -> str:
    return raw if len(raw) <= limit else f"{raw[:limit]}...[truncated {len(raw)-limit} chars]"


def _write_event(call_id: int, event_type: str, payload: dict | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(CallEvent(call_id=call_id, event_type=event_type, event_payload=payload or {}))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("NOMOS_WS_ERROR failed_to_persist call_id=%s event_type=%s", call_id, event_type)
    finally:
        db.close()


def _update_call(call_id: int, **updates: Any) -> None:
    db = SessionLocal()
    try:
        call = db.get(Call, call_id)
        if call:
            for k, v in updates.items():
                setattr(call, k, v)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("NOMOS_WS_ERROR failed_to_update_call call_id=%s", call_id)
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
            "twilio_max_call_duration": _int_setting(ss.get("twilio_max_call_duration", "600"), 600),
            "max_spoken_response_chars": _int_setting(ss.get("max_spoken_response_chars", "220"), 220),
            "stt_flush_bytes": _int_setting(ss.get("stt_flush_bytes", str(DEFAULT_STT_FLUSH_BYTES)), DEFAULT_STT_FLUSH_BYTES),
            "stt_flush_seconds": _float_setting(ss.get("stt_flush_seconds", str(DEFAULT_STT_FLUSH_SECONDS)), DEFAULT_STT_FLUSH_SECONDS),
            "min_stt_buffer_bytes": _int_setting(ss.get("min_stt_buffer_bytes", ss.get("stt_min_audio_bytes", str(MIN_STT_AUDIO_BYTES))), MIN_STT_AUDIO_BYTES),
            "stt_after_bot_cooldown_ms": _int_setting(ss.get("stt_after_bot_cooldown_ms", str(DEFAULT_STT_AFTER_BOT_COOLDOWN_MS)), DEFAULT_STT_AFTER_BOT_COOLDOWN_MS),
            "outbound_audio_queue_max": _int_setting(ss.get("outbound_audio_queue_max", "200"), 200),
            "barge_in_enabled": _bool_setting(ss.get("barge_in_enabled", "false"), False),
        }
    except Exception:
        logger.exception("NOMOS_WS_ERROR failed_to_load_settings")
        return {"voice_safe_mode": True, "allow_greeting_in_safe_mode": False, "greeting_on_start_enabled": True, "text_debug_mode": False, "stt_enabled": False, "agent_enabled": False, "tts_enabled": False, "twilio_max_call_duration": 600, "max_spoken_response_chars": 220, "stt_flush_bytes": DEFAULT_STT_FLUSH_BYTES, "stt_flush_seconds": DEFAULT_STT_FLUSH_SECONDS, "min_stt_buffer_bytes": MIN_STT_AUDIO_BYTES, "stt_after_bot_cooldown_ms": DEFAULT_STT_AFTER_BOT_COOLDOWN_MS, "outbound_audio_queue_max": 200, "barge_in_enabled": False}
    finally:
        db.close()


async def safe_send_json(ws: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except WebSocketDisconnect:
        logger.warning("NOMOS_SAFE_SEND_DISCONNECTED")
        return False
    except RuntimeError as e:
        logger.warning("NOMOS_SAFE_SEND_RUNTIME_ERROR error=%s", e)
        return False
    except Exception:
        logger.exception("NOMOS_SAFE_SEND_ERROR")
        return False


def is_valid_user_transcript(text: str | None) -> bool:
    cleaned = (text or "").strip()
    lowered = cleaned.lower().strip(" .!?")
    if not cleaned or lowered in INVALID_TRANSCRIPTS:
        return False
    if cleaned.startswith("(") and cleaned.endswith(")") and cleaned.count("(") == 1 and cleaned.count(")") == 1:
        return False
    if len(lowered) < 4 and lowered not in MEANINGFUL_SHORT_TRANSCRIPTS:
        return False
    return True


def _is_invalid_transcript(text: str | None) -> bool:
    return not is_valid_user_transcript(text)


def _shorten_response(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(" ", 1)[0].strip()
    return (shortened or text[:max_chars]).rstrip(".,;:") + "…"


def _clear_queue(queue: asyncio.Queue[bytes]) -> int:
    cleared = 0
    while True:
        try:
            queue.get_nowait()
            queue.task_done()
            cleared += 1
        except asyncio.QueueEmpty:
            return cleared


async def queue_twilio_audio(call_id: int, queue: asyncio.Queue[bytes | None], audio: bytes, chunk_size: int = TWILIO_AUDIO_CHUNK_BYTES) -> None:
    chunks = [audio[i:i + chunk_size] for i in range(0, len(audio), chunk_size) if audio[i:i + chunk_size]]
    if not chunks:
        return
    if queue.qsize() + len(chunks) > queue.maxsize:
        dropped = _clear_queue(queue)
        logger.warning("NOMOS_AUDIO_QUEUE_BACKPRESSURE call_id=%s dropped=%s pending_chunks=%s new_chunks=%s", call_id, dropped, queue.qsize(), len(chunks))
        _write_event(call_id, "audio_queue_backpressure", {"dropped": dropped, "chunks": len(chunks)})
    for chunk in chunks:
        try:
            queue.put_nowait(chunk)
        except asyncio.QueueFull:
            logger.warning("NOMOS_AUDIO_QUEUE_BACKPRESSURE call_id=%s action=skip_new_response", call_id)
            _write_event(call_id, "audio_queue_backpressure", {"action": "skip_new_response"})
            return
    try:
        queue.put_nowait(None)
    except asyncio.QueueFull:
        logger.warning("NOMOS_AUDIO_QUEUE_BACKPRESSURE call_id=%s action=missing_response_boundary", call_id)
    logger.warning("NOMOS_AUDIO_QUEUE_PUT bytes=%s chunks=%s", len(audio), len(chunks))
    _write_event(call_id, "twilio_audio_queued", {"bytes": len(audio), "chunks": len(chunks)})


async def twilio_audio_sender(ws: WebSocket, stream_sid_ref: dict[str, str | None], queue: asyncio.Queue[bytes | None], stop_event: asyncio.Event, call_id: int, bot_is_speaking: asyncio.Event, cooldown_until_ref: dict[str, float], stats: dict[str, int], cooldown_ms: int) -> None:
    logger.warning("NOMOS_AUDIO_SENDER_STARTED call_id=%s", call_id)
    chunk_index = 0
    next_send_at = time.monotonic()
    try:
        while not stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if chunk is None:
                queue.task_done()
                if bot_is_speaking.is_set():
                    bot_is_speaking.clear()
                    cooldown_until_ref["until"] = time.monotonic() + (cooldown_ms / 1000)
                    logger.warning("NOMOS_BOT_SPEAKING_END call_id=%s", call_id)
                    _write_event(call_id, "bot_speaking_end", {})
                    await safe_send_json(ws, {"event": "mark", "streamSid": stream_sid_ref.get("stream_sid"), "mark": {"name": f"nomos-tts-complete-{stats.get('tts_responses_queued', 0)}"}})
                continue
            stream_sid = stream_sid_ref.get("stream_sid")
            if not stream_sid:
                queue.task_done()
                continue
            if not bot_is_speaking.is_set():
                bot_is_speaking.set()
                logger.warning("NOMOS_BOT_SPEAKING_START call_id=%s", call_id)
                _write_event(call_id, "bot_speaking_start", {})
            delay = next_send_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            ok = await safe_send_json(ws, {"event": "media", "streamSid": stream_sid, "media": {"payload": base64.b64encode(chunk).decode("ascii")}})
            queue.task_done()
            if not ok:
                logger.warning("NOMOS_AUDIO_SEND_DISCONNECTED call_id=%s", call_id)
                stop_event.set()
                break
            chunk_index += 1
            stats["tts_chunks_sent"] = stats.get("tts_chunks_sent", 0) + 1
            if chunk_index == 1 or chunk_index % MEDIA_EVENT_SAMPLE_CHUNKS == 0:
                logger.warning("NOMOS_AUDIO_CHUNK_SENT chunk_index=%s", chunk_index)
            next_send_at = time.monotonic() + TWILIO_AUDIO_CHUNK_SECONDS
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("NOMOS_AUDIO_SEND_ERROR call_id=%s", call_id)
        stop_event.set()
    finally:
        logger.warning("NOMOS_AUDIO_SENDER_STOPPED call_id=%s", call_id)


async def send_test_greeting(call_id: int) -> bool:
    session = ACTIVE_TWILIO_SESSIONS.get(call_id)
    if not session or not session.get("stream_sid"):
        return False
    asyncio.create_task(_speak_text(call_id, GREETING, "de-DE", session["outbound_audio_queue"]))
    return True


async def _speak_text(call_id: int, text: str, language: str, queue: asyncio.Queue[bytes | None]) -> None:
    logger.warning("NOMOS_TTS_START text_len=%s", len(text))
    _write_event(call_id, "tts_started", {"text_len": len(text)})
    db = SessionLocal()
    try:
        audio = await ElevenLabsService(SettingsService(db, get_settings().app_encryption_key)).text_to_twilio_audio(text, language)
    finally:
        db.close()
    logger.warning("NOMOS_TTS_BYTES bytes=%s", len(audio))
    _write_event(call_id, "tts_completed", {"bytes": len(audio)})
    await queue_twilio_audio(call_id, queue, audio)


async def _process_buffer(call_id: int, stream_sid: str | None, audio: bytes, queue: asyncio.Queue[bytes | None], processing_lock: asyncio.Lock, stats: dict[str, int]) -> None:
    logger.warning("NOMOS_PROCESS_TASK_STARTED call_id=%s bytes=%s", call_id, len(audio))
    try:
        async with processing_lock:
            settings = _settings_snapshot()
            if settings["voice_safe_mode"] or not settings["stt_enabled"]:
                logger.warning("NOMOS_STT_DISABLED call_id=%s voice_safe_mode=%s stt_enabled=%s", call_id, settings["voice_safe_mode"], settings["stt_enabled"])
                return
            min_audio_bytes = settings.get("min_stt_buffer_bytes", MIN_STT_AUDIO_BYTES)
            if len(audio) < min_audio_bytes:
                logger.warning("NOMOS_STT_SKIP_SMALL_BUFFER bytes=%s", len(audio))
                _write_event(call_id, "stt_skip_small_buffer", {"bytes": len(audio), "min_bytes": min_audio_bytes})
                return
            logger.warning("NOMOS_STT_START bytes=%s", len(audio))
            _write_event(call_id, "stt_started", {"bytes": len(audio)})
            transcript_text = None
            language = None
            try:
                db = SessionLocal()
                try:
                    ss = SettingsService(db, get_settings().app_encryption_key)
                    call = db.get(Call, call_id)
                    active_language = call.active_language if call else None
                    transcript_text = await ElevenLabsService(ss).transcribe_chunk(audio, language_code=active_language)
                    logger.warning("NOMOS_STT_RESULT text=%s", transcript_text or "")
                    _write_event(call_id, "stt_completed", {"text": transcript_text or ""})
                    if _is_invalid_transcript(transcript_text):
                        logger.warning("NOMOS_STT_INVALID_TRANSCRIPT call_id=%s text=%s", call_id, transcript_text or "")
                        logger.warning("NOMOS_AGENT_SKIPPED_INVALID_TRANSCRIPT call_id=%s", call_id)
                        _write_event(call_id, "stt_invalid_transcript", {"text": transcript_text or ""})
                        return
                    if call:
                        language = detect_language(transcript_text) if call.case.language_mode == "auto" else call.active_language or call.case.preferred_language
                        call.active_language = language
                        speaker = "ivr" if is_ivr(transcript_text) else "operator"
                        db.add(CallTranscript(call_id=call_id, speaker=speaker, text=transcript_text, language=language, confidence=0.8))
                        db.commit()
                finally:
                    db.close()
            except Exception:
                logger.exception("NOMOS_PROCESS_TASK_ERROR stage=stt call_id=%s", call_id)
                _write_event(call_id, "websocket_error", {"stage": "stt"})
                return
            digit = dtmf_for_ivr(transcript_text)
            if digit and stream_sid:
                logger.warning("NOMOS_DTMF_SKIPPED_BACKGROUND_SEND digit=%s", digit)
                return
            if not settings["agent_enabled"]:
                return
            logger.warning("NOMOS_AGENT_START")
            _write_event(call_id, "agent_started", {"text": transcript_text})
            response = None
            try:
                db = SessionLocal()
                try:
                    call = db.get(Call, call_id)
                    if call:
                        ss = SettingsService(db, get_settings().app_encryption_key)
                        response = await OpenAIAgentService(ss).respond(call.case, transcript_text, language or call.active_language or "de-DE")
                        response = _shorten_response(response or "", settings["max_spoken_response_chars"])
                        db.add(CallTranscript(call_id=call_id, speaker="agent", text=response, language=language or call.active_language))
                        db.commit()
                finally:
                    db.close()
            except Exception:
                logger.exception("NOMOS_PROCESS_TASK_ERROR stage=agent call_id=%s", call_id)
                _write_event(call_id, "websocket_error", {"stage": "agent"})
                return
            logger.warning("NOMOS_AGENT_RESPONSE text=%s", response or "")
            logger.warning("NOMOS_AGENT_RESPONSE_READY text_len=%s", len(response or ""))
            _write_event(call_id, "agent_completed", {"text": response or ""})
            if not response:
                logger.warning('NOMOS_AGENT_TTS_SKIPPED reason="empty_response"')
                _write_event(call_id, "agent_tts_skipped", {"reason": "empty_response"})
            elif settings["text_debug_mode"]:
                logger.warning('NOMOS_AGENT_TTS_SKIPPED reason="text_debug_mode"')
                _write_event(call_id, "agent_tts_skipped", {"reason": "text_debug_mode"})
            elif not settings["tts_enabled"]:
                logger.warning('NOMOS_AGENT_TTS_SKIPPED reason="tts_disabled"')
                _write_event(call_id, "agent_tts_skipped", {"reason": "tts_disabled"})
            else:
                stats["tts_responses_queued"] = stats.get("tts_responses_queued", 0) + 1
                logger.warning("NOMOS_AGENT_TTS_QUEUED text_len=%s", len(response))
                _write_event(call_id, "agent_tts_queued", {"text_len": len(response)})
                await _speak_text(call_id, response, language or "de-DE", queue)
    except Exception:
        logger.exception("NOMOS_PROCESS_TASK_ERROR call_id=%s", call_id)
    finally:
        logger.warning("NOMOS_PROCESS_TASK_FINISHED call_id=%s", call_id)


@router.websocket("/ws/twilio-media/{call_id}")
async def media(ws: WebSocket, call_id: int):
    await ws.accept()
    started = time.time()
    stream_sid_ref: dict[str, str | None] = {"stream_sid": None}
    buffer = bytearray()
    last_flush = time.time()
    connected = True
    stop_event = asyncio.Event()
    processing_lock = asyncio.Lock()
    processing_tasks: set[asyncio.Task] = set()
    bot_is_speaking = asyncio.Event()
    stt_cooldown_until = {"until": 0.0}
    voice_stats = {"tts_responses_queued": 0, "tts_chunks_sent": 0}
    media_chunk_count = 0
    last_media_event_at = time.monotonic()
    settings = _settings_snapshot()
    outbound_audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=settings["outbound_audio_queue_max"])
    sender_task = asyncio.create_task(twilio_audio_sender(ws, stream_sid_ref, outbound_audio_queue, stop_event, call_id, bot_is_speaking, stt_cooldown_until, voice_stats, settings["stt_after_bot_cooldown_ms"]))
    ACTIVE_TWILIO_SESSIONS[call_id] = {"ws": ws, "stream_sid": None, "outbound_audio_queue": outbound_audio_queue, "bot_is_speaking": bot_is_speaking, "voice_stats": voice_stats}
    logger.warning("NOMOS_WS_CONNECTED call_id=%s", call_id)
    logger.warning("NOMOS_RECEIVE_LOOP_STARTED call_id=%s", call_id)
    _write_event(call_id, "websocket_connected", {"voice_safe_mode": settings["voice_safe_mode"]})
    try:
        while connected and not stop_event.is_set():
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                connected = False
                stop_event.set()
                logger.warning("NOMOS_WS_STOP call_id=%s disconnected=true", call_id)
                _write_event(call_id, "websocket_disconnected", {})
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.exception("NOMOS_WS_ERROR call_id=%s stage=json_parse raw=%s", call_id, _safe_raw(raw))
                _write_event(call_id, "websocket_error", {"stage": "json_parse", "raw": _safe_raw(raw)})
                continue
            event = msg.get("event")
            if event != "media":
                logger.warning("NOMOS_WS_RAW event=%s", event)
            try:
                if event == "connected":
                    _write_event(call_id, "twilio_connected_received", {})
                elif event == "start":
                    start = msg.get("start") or {}
                    stream_sid = start.get("streamSid") or msg.get("streamSid")
                    call_sid = start.get("callSid")
                    stream_sid_ref["stream_sid"] = stream_sid
                    ACTIVE_TWILIO_SESSIONS[call_id]["stream_sid"] = stream_sid
                    payload = {"streamSid": stream_sid, "callSid": call_sid, "accountSid": start.get("accountSid"), "customParameters": start.get("customParameters") or {}}
                    logger.warning("NOMOS_TWILIO_START streamSid=%s callSid=%s", stream_sid, call_sid)
                    _write_event(call_id, "twilio_start_received", payload)
                    if call_sid:
                        _update_call(call_id, twilio_call_sid=call_sid, status="in_progress", started_at=datetime.utcnow())
                    if stream_sid and settings["greeting_on_start_enabled"] and (not settings["voice_safe_mode"] or settings["allow_greeting_in_safe_mode"]):
                        task = asyncio.create_task(_speak_text(call_id, GREETING, "de-DE", outbound_audio_queue))
                        processing_tasks.add(task)
                        task.add_done_callback(processing_tasks.discard)
                elif event == "media":
                    mp = msg.get("media") or {}
                    payload = mp.get("payload") or ""
                    chunk = mp.get("chunk")
                    timestamp = mp.get("timestamp")
                    audio = base64.b64decode(payload, validate=True) if payload else b""
                    if settings["barge_in_enabled"] and not outbound_audio_queue.empty():
                        cleared = _clear_queue(outbound_audio_queue)
                        logger.warning("NOMOS_BARGE_IN_CLEAR_AUDIO call_id=%s cleared=%s", call_id, cleared)
                    media_chunk_count += 1
                    should_sample_media = media_chunk_count == 1 or media_chunk_count % MEDIA_EVENT_SAMPLE_CHUNKS == 0 or time.monotonic() - last_media_event_at >= 1.0
                    if should_sample_media:
                        logger.warning("NOMOS_TWILIO_MEDIA chunk=%s timestamp=%s bytes=%s chunk_count=%s", chunk, timestamp, len(audio), media_chunk_count)
                        _write_event(call_id, "twilio_media_received", {"chunk": chunk, "timestamp": timestamp, "bytes": len(audio), "streamSid": msg.get("streamSid") or stream_sid_ref.get("stream_sid"), "chunk_count": media_chunk_count})
                        last_media_event_at = time.monotonic()
                    if bot_is_speaking.is_set():
                        buffer.clear()
                        last_flush = time.time()
                        logger.warning("NOMOS_STT_SUPPRESSED_BOT_SPEAKING chunk=%s", chunk)
                        continue
                    if time.monotonic() < stt_cooldown_until["until"]:
                        buffer.clear()
                        last_flush = time.time()
                        logger.warning("NOMOS_STT_COOLDOWN_ACTIVE")
                        continue
                    buffer.extend(audio)
                    if should_sample_media:
                        logger.warning("NOMOS_STT_BUFFER_BYTES bytes=%s", len(buffer))
                    if len(buffer) >= settings["stt_flush_bytes"] or (buffer and time.time() - last_flush >= settings["stt_flush_seconds"]):
                        if len(buffer) < settings.get("min_stt_buffer_bytes", MIN_STT_AUDIO_BYTES):
                            last_flush = time.time()
                            logger.warning("NOMOS_STT_SKIP_SMALL_BUFFER bytes=%s", len(buffer))
                        elif processing_lock.locked():
                            logger.warning("NOMOS_STT_FLUSH_SKIPPED_PROCESSING_BUSY bytes=%s", len(buffer))
                        else:
                            flushed = bytes(buffer)
                            buffer.clear()
                            last_flush = time.time()
                            logger.warning("NOMOS_STT_FLUSH bytes=%s", len(flushed))
                            task = asyncio.create_task(_process_buffer(call_id, stream_sid_ref.get("stream_sid"), flushed, outbound_audio_queue, processing_lock, voice_stats))
                            processing_tasks.add(task)
                            task.add_done_callback(processing_tasks.discard)
                elif event == "mark":
                    _write_event(call_id, "twilio_mark_received", {"mark": msg.get("mark")})
                elif event == "dtmf":
                    _write_event(call_id, "twilio_dtmf_received", {"dtmf": msg.get("dtmf")})
                elif event == "stop":
                    logger.warning("NOMOS_WS_STOP call_id=%s", call_id)
                    _write_event(call_id, "twilio_stop_received", {"streamSid": msg.get("streamSid") or stream_sid_ref.get("stream_sid"), "stop": msg.get("stop") or {}})
                    _update_call(call_id, status="completed", ended_at=datetime.utcnow())
                    break
                else:
                    _write_event(call_id, "unknown_twilio_event", {"event": event})
            except Exception:
                logger.exception("NOMOS_WS_ERROR call_id=%s stage=event_processing", call_id)
                _write_event(call_id, "websocket_error", {"stage": "event_processing", "event": event})
            if time.time() - started > settings["twilio_max_call_duration"]:
                _write_event(call_id, "twilio_stop_received", {"reason": "max_duration"})
                break
    finally:
        logger.warning("NOMOS_RECEIVE_LOOP_STOPPED call_id=%s", call_id)
        stop_event.set()
        sender_task.cancel()
        await asyncio.gather(sender_task, return_exceptions=True)
        for task in processing_tasks:
            task.cancel()
        if processing_tasks:
            await asyncio.gather(*processing_tasks, return_exceptions=True)
        ACTIVE_TWILIO_SESSIONS.pop(call_id, None)
        if connected:
            _write_event(call_id, "websocket_disconnected", {})

from datetime import datetime
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.agents.action_router_agent import ActionRouterAgent
from app.config import get_settings
from app.database import get_db
from app.models import ActionRun, Call, CallEvent, CallExtraction, CallTranscript, Case
from app.security import current_admin
from app.services.openai_agent_service import OpenAIAgentService
from app.services.twilio_service import TwilioService
from app.services.scenario_templates import scenario_label
from app.routes.websocket import ACTIVE_TWILIO_SESSIONS, is_valid_user_transcript, send_test_greeting, send_test_reply
from app.settings_service import SettingsService

def _bool_setting(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1','true','yes','on','enabled'}

def _voice_debug_summary(db, call_id):
    events = db.query(CallEvent).filter_by(call_id=call_id).all()
    media_events = [e for e in events if e.event_type == 'twilio_media_received']
    media_count = max((int((e.event_payload or {}).get('chunk_count') or 0) for e in media_events), default=0)
    if not media_count:
        media_count = len(media_events)
    last_media = media_events[-1].event_payload.get('timestamp') if media_events and media_events[-1].event_payload else None
    last_error = next((e for e in reversed(events) if e.event_type in {'error','websocket_error'}), None)
    ss = SettingsService(db, get_settings().app_encryption_key)
    session = ACTIVE_TWILIO_SESSIONS.get(call_id) or {}
    stats = session.get('voice_stats') or {}
    last_valid_stt = next((e for e in reversed(events) if e.event_type == 'stt_completed' and is_valid_user_transcript((e.event_payload or {}).get('text'))), None)
    last_invalid_stt = next((e for e in reversed(events) if e.event_type == 'stt_invalid_transcript'), None)
    last_agent_response = next((e for e in reversed(events) if e.event_type == 'agent_completed'), None)
    return {
        'websocket_connected': any(e.event_type == 'websocket_connected' for e in events),
        'twilio_start': any(e.event_type == 'twilio_start_received' for e in events),
        'media_count': media_count,
        'last_media_timestamp': last_media,
        'last_error': last_error,
        'voice_safe_mode': _bool_setting(ss.get('voice_safe_mode', 'true'), True),
        'last_valid_stt_transcript': (last_valid_stt.event_payload or {}).get('text') if last_valid_stt else None,
        'last_invalid_stt_transcript': (last_invalid_stt.event_payload or {}).get('text') if last_invalid_stt else None,
        'last_agent_response': (last_agent_response.event_payload or {}).get('text') if last_agent_response else None,
        'bot_is_speaking': bool(session.get('bot_is_speaking') and session['bot_is_speaking'].is_set()),
        'bot_speaking_started_at': stats.get('bot_speaking_started_at'),
        'bot_speaking_duration': stats.get('last_bot_speaking_duration', 0),
        'stt_suppressed_chunks': stats.get('stt_suppressed_chunks', max((int((e.event_payload or {}).get('chunk_count') or 0) for e in events if e.event_type == 'stt_suppressed_bot_speaking'), default=0)),
        'last_bot_speaking_end_reason': stats.get('last_bot_speaking_end_reason') or ((next((e for e in reversed(events) if e.event_type == 'bot_speaking_end'), None).event_payload or {}).get('reason') if next((e for e in reversed(events) if e.event_type == 'bot_speaking_end'), None) else None),
        'audio_queue_size': session.get('outbound_audio_queue').qsize() if session.get('outbound_audio_queue') else 0,
        'audio_queue_max': stats.get('audio_queue_max') or (session.get('outbound_audio_queue').maxsize if session.get('outbound_audio_queue') else None),
        'tts_responses_accepted': stats.get('tts_responses_accepted', len([e for e in events if e.event_type == 'twilio_audio_queued'])),
        'tts_responses_skipped': stats.get('tts_responses_skipped', len([e for e in events if e.event_type == 'audio_queue_backpressure' and (e.event_payload or {}).get('action') == 'skip_new_response'])),
        'agent_responses_queued_to_tts': stats.get('tts_responses_queued', len([e for e in events if e.event_type == 'agent_tts_queued'])),
        'tts_chunks_sent': stats.get('tts_chunks_sent', len([e for e in events if e.event_type == 'twilio_audio_chunk_sent'])),
        'last_tts_text_length': stats.get('last_tts_text_length'),
        'last_tts_byte_length': stats.get('last_tts_byte_length'),
        'last_tts_duration_estimate': stats.get('last_tts_duration_estimate'),
        'tts_chunks_queued': stats.get('tts_chunks_queued', 0),
        'last_sender_send_duration': stats.get('last_sender_send_duration'),
        'dropped_responses': stats.get('dropped_responses', 0),
        'average_chunk_send_delay': stats.get('average_chunk_send_delay', 0),
    }
router=APIRouter(); templates=Jinja2Templates(directory='app/templates')
@router.get('/calls')
def calls(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    return templates.TemplateResponse(request, 'calls.html', {'title':'Call History','calls':db.query(Call).order_by(Call.created_at.desc()).all()})
@router.get('/calls/{call_id}')
def call_detail(call_id:int, request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    call=db.get(Call,call_id)
    if not call: raise HTTPException(status_code=404, detail='Call not found')
    events=db.query(CallEvent).filter_by(call_id=call_id).all()
    return templates.TemplateResponse(request, 'call_detail.html', {'title':'Call Detail','call':call,'events':events,'voice_debug':_voice_debug_summary(db, call_id),'transcripts':db.query(CallTranscript).filter_by(call_id=call_id).all(),'extractions':db.query(CallExtraction).filter_by(call_id=call_id).all(),'actions':db.query(ActionRun).filter_by(call_id=call_id).all(),'scenario_label':scenario_label})
@router.post('/calls/outbound')
@router.post('/api/calls/outbound')
def outbound(case_id:int=Form(...), db:Session=Depends(get_db), admin=Depends(current_admin)):
    case=db.get(Case,case_id)
    if not case:
        raise HTTPException(status_code=404, detail='Case not found')
    if not case.target_phone_number:
        return '<div class="alert alert-error">Case is missing a target phone number.</div>'
    ss=SettingsService(db,get_settings().app_encryption_key)
    call=Call(case_id=case.id, from_number=ss.get('twilio_phone_number'), to_number=case.target_phone_number, active_language=case.preferred_language if case.language_mode=='fixed' else ss.get('outbound_default_language','de-DE'))
    db.add(call); db.commit(); db.refresh(call)
    try:
        tw=TwilioService(ss).create_call(case.target_phone_number, call.id); call.twilio_call_sid=tw.sid; call.status='initiating'; case.status='calling'; db.add(CallEvent(call_id=call.id,event_type='call_started',event_payload={'twilio_sid':tw.sid}))
    except Exception as e:
        call.status='failed'; call.error_message=str(e); db.add(CallEvent(call_id=call.id,event_type='error',event_payload={'message':str(e)}))
    db.commit()
    if call.status == 'failed':
        return f'<div class="alert alert-error">Call {call.id} failed: {call.error_message}</div>'
    return f'<div class="alert alert-success">Call {call.id} created. SID: {call.twilio_call_sid or "pending"}</div>'

@router.post('/calls/{call_id}/debug-send-greeting')
async def debug_send_greeting(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok = await send_test_greeting(call_id)
    db.add(CallEvent(call_id=call_id, event_type='debug_greeting_requested', event_payload={'success': ok}))
    db.commit()
    return JSONResponse({'success': ok, 'message': 'Greeting sent' if ok else 'No active websocket session for this call'})

@router.post('/calls/{call_id}/debug-send-short-test-reply')
async def debug_send_short_test_reply(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok = await send_test_reply(call_id, "Thanks. I’ll note that.", "en-US", "short_test_reply")
    db.add(CallEvent(call_id=call_id, event_type='debug_short_test_reply_requested', event_payload={'success': ok}))
    db.commit()
    return JSONResponse({'success': ok, 'message': 'Short test reply sent' if ok else 'No active websocket session for this call'})

@router.post('/calls/{call_id}/extract')
async def extract(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    call=db.get(Call,call_id)
    if not call: raise HTTPException(status_code=404, detail='Call not found')
    ss=SettingsService(db,get_settings().app_encryption_key); data=await OpenAIAgentService(ss).extract(call.case, db.query(CallTranscript).filter_by(call_id=call_id).all(), db.query(CallEvent).filter_by(call_id=call_id).all())
    ex=CallExtraction(call_id=call.id, case_id=call.case_id, extracted_json=data, **data); db.add(ex); db.commit(); return 'Extraction saved'
@router.post('/calls/{call_id}/trigger-action')
async def trigger(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    call=db.get(Call,call_id)
    if not call: raise HTTPException(status_code=404, detail='Call not found')
    ex=db.query(CallExtraction).filter_by(call_id=call_id).order_by(CallExtraction.created_at.desc()).first()
    if not ex: return 'No extraction available'
    await ActionRouterAgent(db, SettingsService(db,get_settings().app_encryption_key)).run(call.case, call, ex); return 'Action triggered'

from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request
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
from app.routes.websocket import send_test_greeting
from app.settings_service import SettingsService

def _bool_setting(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1','true','yes','on','enabled'}

def _voice_debug_summary(db, call_id):
    events = db.query(CallEvent).filter_by(call_id=call_id).all()
    media_events = [e for e in events if e.event_type == 'twilio_media_received']
    last_media = media_events[-1].event_payload.get('timestamp') if media_events and media_events[-1].event_payload else None
    last_error = next((e for e in reversed(events) if e.event_type in {'error','websocket_error'}), None)
    ss = SettingsService(db, get_settings().app_encryption_key)
    return {
        'websocket_connected': any(e.event_type == 'websocket_connected' for e in events),
        'twilio_start': any(e.event_type == 'twilio_start_received' for e in events),
        'media_count': len(media_events),
        'last_media_timestamp': last_media,
        'last_error': last_error,
        'voice_safe_mode': _bool_setting(ss.get('voice_safe_mode', 'true'), True),
    }
router=APIRouter(); templates=Jinja2Templates(directory='app/templates')
@router.get('/calls')
def calls(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    return templates.TemplateResponse(request, 'calls.html', {'title':'Call History','calls':db.query(Call).order_by(Call.created_at.desc()).all()})
@router.get('/calls/{call_id}')
def call_detail(call_id:int, request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    call=db.get(Call,call_id)
    events=db.query(CallEvent).filter_by(call_id=call_id).all()
    return templates.TemplateResponse(request, 'call_detail.html', {'title':'Call Detail','call':call,'events':events,'voice_debug':_voice_debug_summary(db, call_id),'transcripts':db.query(CallTranscript).filter_by(call_id=call_id).all(),'extractions':db.query(CallExtraction).filter_by(call_id=call_id).all(),'actions':db.query(ActionRun).filter_by(call_id=call_id).all()})
@router.post('/calls/outbound')
@router.post('/api/calls/outbound')
def outbound(case_id:int=Form(...), db:Session=Depends(get_db), admin=Depends(current_admin)):
    case=db.get(Case,case_id); ss=SettingsService(db,get_settings().app_encryption_key)
    call=Call(case_id=case.id, from_number=ss.get('twilio_phone_number'), to_number=case.target_phone_number, active_language=case.preferred_language if case.language_mode=='fixed' else ss.get('outbound_default_language','de-DE'))
    db.add(call); db.commit(); db.refresh(call)
    try:
        tw=TwilioService(ss).create_call(case.target_phone_number, call.id); call.twilio_call_sid=tw.sid; call.status='initiating'; case.status='calling'; db.add(CallEvent(call_id=call.id,event_type='call_started',event_payload={'twilio_sid':tw.sid}))
    except Exception as e:
        call.status='failed'; call.error_message=str(e); db.add(CallEvent(call_id=call.id,event_type='error',event_payload={'message':str(e)}))
    db.commit(); msg=f'Call {call.id} created. SID: {call.twilio_call_sid or call.error_message}'
    return msg

@router.post('/calls/{call_id}/debug-send-greeting')
async def debug_send_greeting(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok = await send_test_greeting(call_id)
    db.add(CallEvent(call_id=call_id, event_type='debug_greeting_requested', event_payload={'success': ok}))
    db.commit()
    return JSONResponse({'success': ok, 'message': 'Greeting sent' if ok else 'No active websocket session for this call'})

@router.post('/calls/{call_id}/extract')
async def extract(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    call=db.get(Call,call_id); ss=SettingsService(db,get_settings().app_encryption_key); data=await OpenAIAgentService(ss).extract(call.case, db.query(CallTranscript).filter_by(call_id=call_id).all(), db.query(CallEvent).filter_by(call_id=call_id).all())
    ex=CallExtraction(call_id=call.id, case_id=call.case_id, extracted_json=data, **data); db.add(ex); db.commit(); return 'Extraction saved'
@router.post('/calls/{call_id}/trigger-action')
async def trigger(call_id:int, db:Session=Depends(get_db), admin=Depends(current_admin)):
    call=db.get(Call,call_id); ex=db.query(CallExtraction).filter_by(call_id=call_id).order_by(CallExtraction.created_at.desc()).first()
    if not ex: return 'No extraction available'
    await ActionRouterAgent(db, SettingsService(db,get_settings().app_encryption_key)).run(call.case, call, ex); return 'Action triggered'

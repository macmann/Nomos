from datetime import datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.config import get_settings
from app.database import get_db
from app.models import Call, CallEvent
from app.services.twilio_service import TwilioService
from app.settings_service import SettingsService
router=APIRouter()
@router.post('/twilio/voice/{call_id}')
def voice(call_id:int, db:Session=Depends(get_db)):
    call=db.get(Call,call_id); base=get_settings().app_base_url.replace('https://','').replace('http://','').rstrip('/')
    xml=TwilioService.twiml(f'wss://{base}/ws/twilio-media/{call_id}', call_id, call.case_id)
    return Response(xml, media_type='application/xml')
@router.post('/twilio/status')
async def status(request:Request, db:Session=Depends(get_db)):
    form=await request.form(); sid=form.get('CallSid'); call=db.query(Call).filter_by(twilio_call_sid=sid).first()
    if call:
        call.status=form.get('CallStatus') or call.status
        if call.status in ['in-progress','answered'] and not call.started_at: call.started_at=datetime.utcnow()
        if call.status in ['completed','failed','busy','no-answer','canceled']:
            call.ended_at=datetime.utcnow(); call.duration_seconds=int(form.get('CallDuration') or 0)
        if form.get('ErrorMessage'): call.error_message=form.get('ErrorMessage')
        db.add(CallEvent(call_id=call.id,event_type='call_ended' if call.ended_at else 'call_started',event_payload=dict(form))); db.commit()
    return Response('ok')
@router.post('/twilio/recording')
async def recording(request:Request, db:Session=Depends(get_db)):
    form=await request.form(); sid=form.get('CallSid'); call=db.query(Call).filter_by(twilio_call_sid=sid).first()
    if call: call.recording_url=form.get('RecordingUrl'); db.commit()
    return Response('ok')

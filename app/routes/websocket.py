import base64, json, time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.agents.action_router_agent import ActionRouterAgent
from app.config import get_settings
from app.database import SessionLocal
from app.models import Call, CallEvent, CallExtraction, CallTranscript
from app.services.elevenlabs_service import ElevenLabsService
from app.services.language_service import detect_language, dtmf_for_ivr, is_ivr
from app.services.openai_agent_service import OpenAIAgentService
from app.settings_service import SettingsService
router=APIRouter()
@router.websocket('/ws/twilio-media/{call_id}')
async def media(ws:WebSocket, call_id:int):
    await ws.accept(); db=SessionLocal(); stream_sid=None; started=time.time()
    try:
        call=db.get(Call,call_id); ss=SettingsService(db,get_settings().app_encryption_key); db.add(CallEvent(call_id=call_id,event_type='call_started',event_payload={'websocket':'connected'})); db.commit()
        while True:
            msg=json.loads(await ws.receive_text()); event=msg.get('event')
            if event=='start': stream_sid=msg.get('start',{}).get('streamSid')
            elif event=='media':
                audio=base64.b64decode(msg.get('media',{}).get('payload',''))
                text=await ElevenLabsService(ss).transcribe_chunk(audio)
                if not text: continue
                lang=detect_language(text) if call.case.language_mode=='auto' else call.active_language or call.case.preferred_language
                call.detected_language=lang if call.case.language_mode=='auto' else call.detected_language; call.active_language=lang
                speaker='ivr' if is_ivr(text) else 'operator'; db.add(CallTranscript(call_id=call_id,speaker=speaker,text=text,language=lang,confidence=0.8)); db.add(CallEvent(call_id=call_id,event_type='stt_final',event_payload={'text':text}))
                digit=dtmf_for_ivr(text) if speaker=='ivr' else None
                if digit:
                    await ws.send_json({'event':'dtmf','streamSid':stream_sid,'dtmf':{'digits':digit}}); db.add(CallEvent(call_id=call_id,event_type='dtmf_sent',event_payload={'digit':digit}))
                else:
                    response=await OpenAIAgentService(ss).respond(call.case,text,lang); db.add(CallTranscript(call_id=call_id,speaker='agent',text=response,language=lang)); db.add(CallEvent(call_id=call_id,event_type='agent_response',event_payload={'text':response}))
                    for payload in await ElevenLabsService(ss).text_to_twilio_frames(response,lang): await ws.send_json({'event':'media','streamSid':stream_sid,'media':{'payload':payload}})
                db.commit()
            elif event=='stop': break
            if time.time()-started > int(ss.get('twilio_max_call_duration','600')): break
    except WebSocketDisconnect: pass
    finally:
        call=db.get(Call,call_id)
        if call:
            data=await OpenAIAgentService(SettingsService(db,get_settings().app_encryption_key)).extract(call.case, db.query(CallTranscript).filter_by(call_id=call_id).all(), db.query(CallEvent).filter_by(call_id=call_id).all())
            ex=CallExtraction(call_id=call.id,case_id=call.case_id,extracted_json=data,**data); db.add(ex); db.commit(); await ActionRouterAgent(db, SettingsService(db,get_settings().app_encryption_key)).run(call.case, call, ex)
        db.close()

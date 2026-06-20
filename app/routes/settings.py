from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import get_settings
from app.database import get_db
from app.security import current_admin
from app.services.elevenlabs_service import ElevenLabsService
from app.services.mcp_mail_service import MCPMailService
from app.services.openai_agent_service import OpenAIAgentService
from app.services.twilio_service import TwilioService
from app.settings_service import SECRET_KEYS, SettingsService
router=APIRouter(); templates=Jinja2Templates('app/templates')
SECTIONS={'OpenAI':['openai_api_key','openai_model','openai_temperature','openai_max_turns','agent_system_prompt','extraction_prompt'],'ElevenLabs':['elevenlabs_api_key','elevenlabs_stt_model','elevenlabs_de_voice_id','elevenlabs_en_voice_id','elevenlabs_tts_model','speech_speed','number_reading_mode'],'Twilio':['twilio_account_sid','twilio_auth_token','twilio_phone_number','twilio_webhook_base_url','twilio_max_call_duration','twilio_recording_enabled'],'Language':['default_language','outbound_default_language','auto_detect_language'],'Compliance':['ai_disclosure_required','ai_disclosure_de','ai_disclosure_en','fake_data_only'],'MCP Mail':['mcp_server_url','mcp_auth_token','mcp_connection_id','mcp_sender_email','mcp_template_de','mcp_template_en','mcp_real_email_enabled']}
TESTS={'OpenAI':'openai','ElevenLabs':'elevenlabs','Twilio':'twilio','Language':'openai','Compliance':'openai','MCP Mail':'mcp-mail'}
@router.get('/settings')
def page(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    ss=SettingsService(db,get_settings().app_encryption_key); return templates.TemplateResponse('settings.html', {'request':request,'title':'Settings','sections':SECTIONS,'tests':TESTS,'values':ss.all_masked()})
@router.post('/settings')
async def save(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    form=await request.form(); ss=SettingsService(db,get_settings().app_encryption_key)
    for k,v in form.items(): ss.set(k, str(v), k in SECRET_KEYS)
    return RedirectResponse('/settings',303)
@router.post('/settings/test/openai')
async def test_openai(db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok,msg=await OpenAIAgentService(SettingsService(db,get_settings().app_encryption_key)).test(); return ('✅ ' if ok else '❌ ')+msg
@router.post('/settings/test/elevenlabs')
async def test_el(db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok,msg=await ElevenLabsService(SettingsService(db,get_settings().app_encryption_key)).test(); return ('✅ ' if ok else '❌ ')+msg
@router.post('/settings/test/twilio')
def test_tw(db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok,msg=TwilioService(SettingsService(db,get_settings().app_encryption_key)).test(); return ('✅ ' if ok else '❌ ')+msg
@router.post('/settings/test/mcp-mail')
async def test_mcp(db:Session=Depends(get_db), admin=Depends(current_admin)):
    ok,msg=await MCPMailService(SettingsService(db,get_settings().app_encryption_key),db).test(); return ('✅ ' if ok else '❌ ')+msg

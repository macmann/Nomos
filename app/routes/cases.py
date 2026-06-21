import json
from datetime import datetime
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import get_settings
from app.database import get_db
from app.models import ActionRun, Call, CallEvent, CallExtraction, Case, Profile
from app.security import current_admin
from app.services.scenario_templates import DEFAULT_SCENARIO, SCENARIOS, get_scenario, normalize_scenario, scenario_label
from app.services.twilio_service import TwilioService
from app.settings_service import SettingsService
router=APIRouter(); templates=Jinja2Templates(directory='app/templates')
STATUSES=['pending','calling','resolved','needs_manual_review','customer_action_required','escalated','failed','closed']
FIELDS=['profile_id','scenario','external_case_id','case_type','process_step','customer_name','customer_email','customer_address','meter_number','market_location_number','grid_operator_name','target_phone_number','problem_description','required_outcome','language_mode','preferred_language']
@router.get('/cases')
def list_cases(request:Request, status:str='', case_type:str='', process_step:str='', language:str='', db:Session=Depends(get_db), admin=Depends(current_admin)):
    q=db.query(Case)
    if status: q=q.filter_by(status=status)
    if case_type: q=q.filter_by(case_type=case_type)
    if process_step: q=q.filter_by(process_step=process_step)
    if language: q=q.filter_by(preferred_language=language)
    cases=q.order_by(Case.created_at.desc()).all(); tpl='partials/case_table.html' if request.headers.get('hx-request') else 'cases.html'
    return templates.TemplateResponse(request, tpl, {'title':'Cases','cases':cases})
@router.get('/cases/new')
def new_case(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    profiles=db.query(Profile).order_by(Profile.name.asc()).all()
    return templates.TemplateResponse(request, 'case_form.html', {'title':'Create Case','scenarios':SCENARIOS,'default_scenario':DEFAULT_SCENARIO,'profiles':profiles})
@router.post('/cases')
async def create_case(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    form=await request.form(); data={k:form.get(k) or None for k in FIELDS}; data['scenario']=normalize_scenario(data.get('scenario')); scenario=get_scenario(data['scenario'])
    profile = db.get(Profile, int(data['profile_id'])) if data.get('profile_id') else None
    if profile:
        for field in ['customer_name','customer_email','customer_address','meter_number','market_location_number','grid_operator_name','target_phone_number','language_mode','preferred_language']:
            if not data.get(field): data[field]=getattr(profile, field)
    if not data.get('case_type'): data['case_type']=scenario['case_type']
    if not data.get('problem_description'): data['problem_description']=scenario['problem_description']
    if not data.get('required_outcome'): data['required_outcome']=scenario['required_outcome']
    date=form.get('registration_sent_at')
    if date:
        try: data['registration_sent_at']=datetime.fromisoformat(date)
        except ValueError: pass
    data['profile_id'] = int(data['profile_id']) if data.get('profile_id') else None
    case=Case(**data); db.add(case); db.commit(); db.refresh(case)
    if form.get('start_call') and case.target_phone_number:
        ss=SettingsService(db,get_settings().app_encryption_key)
        call=Call(case_id=case.id, from_number=ss.get('twilio_phone_number'), to_number=case.target_phone_number, active_language=case.preferred_language if case.language_mode=='fixed' else ss.get('outbound_default_language','de-DE'))
        db.add(call); db.commit(); db.refresh(call)
        try:
            tw=TwilioService(ss).create_call(case.target_phone_number, call.id); call.twilio_call_sid=tw.sid; call.status='initiating'; case.status='calling'; db.add(CallEvent(call_id=call.id,event_type='call_started',event_payload={'twilio_sid':tw.sid}))
        except Exception as e:
            call.status='failed'; call.error_message=str(e); db.add(CallEvent(call_id=call.id,event_type='error',event_payload={'message':str(e)}))
        db.commit(); return RedirectResponse(f'/cases/{case.id}',303)
    return RedirectResponse('/cases',303)
@router.get('/cases/import-fixtures')
def import_page(request:Request, admin=Depends(current_admin)): return templates.TemplateResponse(request, 'import.html', {'title':'Import Fixtures'})
@router.post('/cases/import-fixtures')
async def import_fixtures(request:Request, file:UploadFile=File(...), db:Session=Depends(get_db), admin=Depends(current_admin)):
    try:
        raw=await file.read(); payload=json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return templates.TemplateResponse(request, 'import.html', {'title':'Import Fixtures','error':'Upload a valid UTF-8 JSON fixtures file.'}, status_code=400)
    items=payload.get('cases', payload if isinstance(payload,list) else [])
    if not isinstance(items, list):
        return templates.TemplateResponse(request, 'import.html', {'title':'Import Fixtures','error':'Fixtures must be a JSON array or an object with a cases array.'}, status_code=400)
    count=0
    for item in items:
        if not isinstance(item, dict):
            continue
        data={k:item.get(k) for k in FIELDS if k in item}; data['scenario']=normalize_scenario(data.get('scenario')); scenario=get_scenario(data['scenario'])
        
        if not data.get('case_type'): data['case_type']=scenario['case_type']
        if not data.get('problem_description'): data['problem_description']=scenario['problem_description']
        if not data.get('required_outcome'): data['required_outcome']=scenario['required_outcome']
        db.add(Case(**data)); count+=1
    db.commit(); return templates.TemplateResponse(request, 'import.html', {'title':'Import Fixtures','result':f'Imported {count} cases'})
@router.get('/cases/{case_id}')
def detail(case_id:int, request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    c=db.get(Case,case_id)
    if not c: raise HTTPException(status_code=404, detail='Case not found')
    calls=db.query(Call).filter_by(case_id=case_id).all(); ex=db.query(CallExtraction).filter_by(case_id=case_id).order_by(CallExtraction.created_at.desc()).first(); ar=db.query(ActionRun).filter_by(case_id=case_id).order_by(ActionRun.created_at.desc()).first()
    return templates.TemplateResponse(request, 'case_detail.html', {'title':'Case Detail','case':c,'calls':calls,'extraction':ex,'action':ar,'statuses':STATUSES,'scenario_label':scenario_label,'scenario':get_scenario(c.scenario)})
@router.post('/cases/{case_id}/status')
async def status(case_id:int, request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    form=await request.form(); c=db.get(Case,case_id)
    if not c: raise HTTPException(status_code=404, detail='Case not found')
    c.status=form.get('status','pending'); db.commit(); return RedirectResponse(f'/cases/{case_id}',303)

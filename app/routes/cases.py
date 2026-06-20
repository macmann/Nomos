import json
from datetime import datetime
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import ActionRun, Call, CallExtraction, Case
from app.security import current_admin
router=APIRouter(); templates=Jinja2Templates('app/templates')
STATUSES=['pending','calling','resolved','customer_action_required','escalated','failed','closed']
FIELDS=['external_case_id','case_type','process_step','customer_name','customer_email','customer_address','meter_number','market_location_number','grid_operator_name','target_phone_number','problem_description','required_outcome','language_mode','preferred_language']
@router.get('/cases')
def list_cases(request:Request, status:str='', case_type:str='', process_step:str='', language:str='', db:Session=Depends(get_db), admin=Depends(current_admin)):
    q=db.query(Case)
    if status: q=q.filter_by(status=status)
    if case_type: q=q.filter_by(case_type=case_type)
    if process_step: q=q.filter_by(process_step=process_step)
    if language: q=q.filter_by(preferred_language=language)
    cases=q.order_by(Case.created_at.desc()).all(); tpl='partials/case_table.html' if request.headers.get('hx-request') else 'cases.html'
    return templates.TemplateResponse(tpl, {'request':request,'title':'Cases','cases':cases})
@router.get('/cases/new')
def new_case(request:Request, admin=Depends(current_admin)): return templates.TemplateResponse('case_form.html', {'request':request,'title':'Create Case'})
@router.post('/cases')
async def create_case(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    form=await request.form(); data={k:form.get(k) or None for k in FIELDS}; date=form.get('registration_sent_at')
    if date:
        try: data['registration_sent_at']=datetime.fromisoformat(date)
        except ValueError: pass
    db.add(Case(**data)); db.commit(); return RedirectResponse('/cases',303)
@router.get('/cases/import-fixtures')
def import_page(request:Request, admin=Depends(current_admin)): return templates.TemplateResponse('import.html', {'request':request,'title':'Import Fixtures'})
@router.post('/cases/import-fixtures')
async def import_fixtures(request:Request, file:UploadFile=File(...), db:Session=Depends(get_db), admin=Depends(current_admin)):
    raw=await file.read(); payload=json.loads(raw.decode()); items=payload.get('cases', payload if isinstance(payload,list) else [])
    count=0
    for item in items:
        data={k:item.get(k) for k in FIELDS if k in item}; db.add(Case(**data)); count+=1
    db.commit(); return templates.TemplateResponse('import.html', {'request':request,'title':'Import Fixtures','result':f'Imported {count} cases'})
@router.get('/cases/{case_id}')
def detail(case_id:int, request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    c=db.get(Case,case_id); calls=db.query(Call).filter_by(case_id=case_id).all(); ex=db.query(CallExtraction).filter_by(case_id=case_id).order_by(CallExtraction.created_at.desc()).first(); ar=db.query(ActionRun).filter_by(case_id=case_id).order_by(ActionRun.created_at.desc()).first()
    return templates.TemplateResponse('case_detail.html', {'request':request,'title':'Case Detail','case':c,'calls':calls,'extraction':ex,'action':ar,'statuses':STATUSES})
@router.post('/cases/{case_id}/status')
async def status(case_id:int, request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    form=await request.form(); c=db.get(Case,case_id); c.status=form.get('status','pending'); db.commit(); return RedirectResponse(f'/cases/{case_id}',303)

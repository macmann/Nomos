from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Case, Call
from app.security import current_admin
router=APIRouter(); templates=Jinja2Templates('app/templates')
@router.get('/')
def dashboard(request:Request, db:Session=Depends(get_db), admin=Depends(current_admin)):
    total=db.query(Case).count(); resolved=db.query(Case).filter_by(status='resolved').count(); failed=db.query(Call).filter_by(status='failed').count()
    avg=db.query(func.avg(Call.duration_seconds)).scalar() or 0
    stats={'Total cases':total,'Pending cases':db.query(Case).filter_by(status='pending').count(),'Active calls':db.query(Call).filter(Call.status.in_(['initiating','ringing','in-progress'])).count(),'Resolved cases':resolved,'Customer-action-required cases':db.query(Case).filter_by(status='customer_action_required').count(),'Failed calls':failed,'Escalated cases':db.query(Case).filter_by(status='escalated').count(),'Average call duration':round(avg,1),'Resolution rate':f'{round((resolved/total*100) if total else 0,1)}%'}
    return templates.TemplateResponse('dashboard.html', {'request':request,'title':'Dashboard','stats':stats})

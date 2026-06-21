from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Profile
from app.security import current_admin

router = APIRouter()
templates = Jinja2Templates(directory='app/templates')

PROFILE_FIELDS = [
    'name', 'customer_name', 'customer_email', 'customer_address', 'meter_number',
    'market_location_number', 'grid_operator_name', 'target_phone_number',
    'language_mode', 'preferred_language', 'notes'
]

@router.get('/profiles')
def list_profiles(request: Request, db: Session = Depends(get_db), admin=Depends(current_admin)):
    profiles = db.query(Profile).order_by(Profile.updated_at.desc()).all()
    return templates.TemplateResponse(request, 'profiles.html', {'title': 'Profiles', 'profiles': profiles})

@router.get('/profiles/new')
def new_profile(request: Request, admin=Depends(current_admin)):
    return templates.TemplateResponse(request, 'profile_form.html', {'title': 'Create Profile', 'profile': None})

@router.post('/profiles')
async def create_profile(request: Request, db: Session = Depends(get_db), admin=Depends(current_admin)):
    form = await request.form()
    data = {field: form.get(field) or None for field in PROFILE_FIELDS}
    data['language_mode'] = data.get('language_mode') or 'fixed'
    data['preferred_language'] = data.get('preferred_language') or 'de-DE'
    db.add(Profile(**data))
    db.commit()
    return RedirectResponse('/profiles', 303)

@router.get('/profiles/{profile_id}/edit')
def edit_profile(profile_id: int, request: Request, db: Session = Depends(get_db), admin=Depends(current_admin)):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail='Profile not found')
    return templates.TemplateResponse(request, 'profile_form.html', {'title': 'Edit Profile', 'profile': profile})

@router.post('/profiles/{profile_id}')
async def update_profile(profile_id: int, request: Request, db: Session = Depends(get_db), admin=Depends(current_admin)):
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail='Profile not found')
    form = await request.form()
    for field in PROFILE_FIELDS:
        setattr(profile, field, form.get(field) or None)
    profile.language_mode = profile.language_mode or 'fixed'
    profile.preferred_language = profile.preferred_language or 'de-DE'
    db.commit()
    return RedirectResponse('/profiles', 303)

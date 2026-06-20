from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.security import COOKIE, create_session, logout_response, verify_login
router=APIRouter(); templates=Jinja2Templates(directory='app/templates')
@router.get('/login')
def login_page(request:Request): return templates.TemplateResponse(request, 'login.html')
@router.post('/login')
def login(request:Request, username:str=Form(...), password:str=Form(...), db:Session=Depends(get_db)):
    if not verify_login(username,password): return templates.TemplateResponse(request, 'login.html', {'error':'Invalid credentials'}, status_code=401)
    r=RedirectResponse('/',303); r.set_cookie(COOKIE, create_session(db,username), httponly=True, samesite='lax'); return r
@router.post('/logout')
def logout(request:Request, db:Session=Depends(get_db)): return logout_response(db, request.cookies.get(COOKIE))

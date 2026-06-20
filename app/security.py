import secrets
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .config import get_settings
from .database import get_db
from .models import AdminSession
COOKIE="nomos_session"

def create_session(db: Session, username: str) -> str:
    token=secrets.token_urlsafe(32); db.add(AdminSession(session_token=token, username=username, expires_at=datetime.utcnow()+timedelta(hours=12))); db.commit(); return token

def current_admin(request: Request, db: Session=Depends(get_db)):
    token=request.cookies.get(COOKIE)
    row=db.query(AdminSession).filter(AdminSession.session_token==token, AdminSession.expires_at>datetime.utcnow()).first() if token else None
    if not row: raise HTTPException(status_code=303, headers={"Location":"/login"})
    return row.username

def verify_login(username, password):
    s=get_settings(); return secrets.compare_digest(username,s.admin_username) and secrets.compare_digest(password,s.admin_password)

def logout_response(db: Session, token: str|None):
    if token: db.query(AdminSession).filter_by(session_token=token).delete(); db.commit()
    r=RedirectResponse("/login",303); r.delete_cookie(COOKIE); return r

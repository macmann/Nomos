import logging
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .database import init_db
from .routes import auth, dashboard, cases, calls, settings, twilio, websocket
app=FastAPI(title='Nomos Clearing Voice Agent')
app.mount('/static', StaticFiles(directory='app/static'), name='static')
@app.on_event('startup')
def startup():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    init_db()
@app.get('/health')
def health(): return {'status':'ok'}
@app.head('/')
def root_head(): return JSONResponse(content=None)
for r in [auth.router,dashboard.router,cases.router,calls.router,settings.router,twilio.router,websocket.router]: app.include_router(r)

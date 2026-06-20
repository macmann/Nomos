from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import init_db
from .routes import auth, dashboard, cases, calls, settings, twilio, websocket
app=FastAPI(title='Nomos Clearing Voice Agent')
app.mount('/static', StaticFiles(directory='app/static'), name='static')
@app.on_event('startup')
def startup(): init_db()
for r in [auth.router,dashboard.router,cases.router,calls.router,settings.router,twilio.router,websocket.router]: app.include_router(r)

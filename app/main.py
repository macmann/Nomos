import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .database import init_db
from .routes import auth, dashboard, cases, calls, profiles, settings, twilio, websocket

app = FastAPI(title='Nomos Clearing Voice Agent')
templates = Jinja2Templates(directory='app/templates')
app.mount('/static', StaticFiles(directory='app/static'), name='static')


@app.on_event('startup')
def startup():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    init_db()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    location = (exc.headers or {}).get('Location')
    if location and status.HTTP_300_MULTIPLE_CHOICES <= exc.status_code < status.HTTP_400_BAD_REQUEST:
        return RedirectResponse(location, status_code=exc.status_code)
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request,
        'error.html',
        {
            'title': f'Error {exc.status_code}',
            'heading': 'Page not found' if exc.status_code == status.HTTP_404_NOT_FOUND else 'Request failed',
            'message': exc.detail or 'The requested page could not be loaded.',
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    message = 'Please check the submitted fields and try again.'
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail': message, 'errors': exc.errors()}, status_code=422)
    return templates.TemplateResponse(request, 'error.html', {'title': 'Invalid request', 'heading': 'Invalid request', 'message': message}, status_code=422)


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.head('/')
def root_head():
    return JSONResponse(content=None)


for r in [auth.router, dashboard.router, profiles.router, cases.router, calls.router, settings.router, twilio.router, websocket.router]:
    app.include_router(r)

# Nomos Clearing Voice Agent

Production-style MVP FastAPI application for outbound Nomos energy-market clearing calls. It includes a Jinja/HTMX/Tailwind admin UI, encrypted provider settings, SQLAlchemy models, Twilio outbound calling and webhooks, a Twilio Media Streams websocket pipeline, ElevenLabs STT/TTS service boundaries, OpenAI agent service boundaries, post-call extraction, action routing, and MCP mail support.

## Local run

1. Create a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and set only these variables:

```bash
DATABASE_URL=sqlite:///./nomos.db
APP_ENCRYPTION_KEY=<fernet key or any strong secret>
APP_BASE_URL=http://localhost:8000
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
SESSION_SECRET_KEY=change-me
```

4. Start the app:

```bash
uvicorn app.main:app --reload
```

The app auto-creates tables at startup if they do not exist.

## Neon database

1. Create a Neon project.
2. Copy the pooled PostgreSQL connection string.
3. Use it as `DATABASE_URL` on Render. The app converts standard `postgresql://` URLs to the psycopg SQLAlchemy driver automatically.

## Render deployment

1. Push this repository to GitHub.
2. In Render, create a Blueprint from `render.yaml` or a Python Web Service.
3. Build command: `pip install -r requirements.txt`.
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Set the required environment variables: `DATABASE_URL`, `APP_ENCRYPTION_KEY`, `APP_BASE_URL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `SESSION_SECRET_KEY`.

## Login

Open the deployed URL and log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`. A secure HTTP-only session cookie protects admin pages.

## Configure providers

Go to **Settings** and configure OpenAI, ElevenLabs, Twilio, language, compliance, and MCP Mail values. Provider secrets are encrypted with `cryptography.fernet` and rendered as masked values in the UI.

## Test providers

Each provider section has a **Test connection** button. OpenAI, ElevenLabs, Twilio, and MCP Mail tests use the saved settings and do not expose secret tokens.

## Import fixtures

Open **Import Fixtures**, upload a JSON array or an object with a `cases` array, and the app inserts case rows.

## Create a case

Open **Cases → Create Case**, fill the clearing metadata, target phone number, language mode, and preferred language, then submit.

## Start an outbound call

From the case list or case detail page, click **Start outbound call**. The app creates a call row, loads Twilio settings, calls Twilio, points Twilio to `/twilio/voice/{call_id}`, and updates the case to `calling`.

## Twilio media stream

`/twilio/voice/{call_id}` returns TwiML that connects a bidirectional Media Stream to `/ws/twilio-media/{call_id}`. The websocket persists media events, transcript rows when STT returns final text, agent responses, generated TTS events, and post-call extraction/action results. The actual low-latency STT/TTS audio transcoding seams are isolated in `app/services/elevenlabs_service.py`.

## View transcript and extraction

Open **Calls**, select a call, and review metadata, events, transcript, structured extraction, plain-language note, and action history. Use **Rerun extraction** to regenerate the extraction from saved transcript/events.

## Trigger customer email action

If an extraction has `next_action = trigger_customer_email_agent`, click **Trigger next action manually** or let the post-call router run. MCP Mail uses saved server settings and supports dry-run mode through `mcp_real_email_enabled=false`.

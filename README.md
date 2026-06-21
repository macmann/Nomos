Demo URL: https://nomos-voice-agent.onrender.com
Demo Usernam: admin 
Demo Password: password

# Nomos Clearing Voice Agent

Nomos Clearing Voice Agent is a production-style MVP for outbound energy-market clearing calls. It gives an operations team a secured web dashboard for managing customer/profile fixtures, starting Twilio outbound calls to grid operators, streaming call audio through a voice pipeline, saving transcripts/events, extracting structured post-call outcomes, and routing follow-up actions such as customer emails.

The application is intentionally structured so jury reviewers can inspect the end-to-end workflow without needing live telephony credentials: provider integrations are isolated behind service modules, default settings keep voice processing in safe mode, and the database is created automatically on first startup.

## Table of contents

- [Core capabilities](#core-capabilities)
- [Architecture overview](#architecture-overview)
- [Repository structure](#repository-structure)
- [Technology, APIs, frameworks, and tools](#technology-apis-frameworks-and-tools)
- [Prerequisites](#prerequisites)
- [Local setup and installation](#local-setup-and-installation)
- [Configuration](#configuration)
- [Running the app](#running-the-app)
- [Using the admin UI](#using-the-admin-ui)
- [HTTP and websocket API reference](#http-and-websocket-api-reference)
- [Voice pipeline details](#voice-pipeline-details)
- [Data model](#data-model)
- [Testing and validation](#testing-and-validation)
- [Deployment](#deployment)
- [Security and compliance notes](#security-and-compliance-notes)
- [Troubleshooting](#troubleshooting)

## Core capabilities

- Password-protected admin dashboard with session cookies.
- Case and profile management for energy-market clearing scenarios.
- Fixture import from JSON arrays or objects containing a `cases` array.
- Outbound Twilio call creation from cases.
- Twilio webhook endpoint that returns TwiML for bidirectional Media Streams.
- Websocket media-stream handling for Twilio audio events.
- Safe-mode voice testing so reviewers can verify websocket/media stability without enabling STT, LLM, or TTS.
- ElevenLabs service boundary for speech-to-text and text-to-speech.
- OpenAI service boundary for live call responses and post-call extraction.
- Structured post-call extraction saved to the database.
- Action router for case updates, escalation, closure, and MCP Mail customer email follow-up.
- Encrypted provider settings for OpenAI, ElevenLabs, Twilio, and MCP Mail secrets.
- SQLite by default with PostgreSQL/Neon support for hosted deployments.

## Architecture overview

```text
Browser/Admin User
      |
      v
FastAPI app (app.main)
      |
      +-- Jinja2 + HTMX-style admin pages
      +-- SQLAlchemy ORM + SQLite/PostgreSQL
      +-- SettingsService with encrypted provider secrets
      |
      +-- Twilio REST API for outbound calls
      |       |
      |       v
      |   Twilio voice webhook (/twilio/voice/{call_id})
      |       |
      |       v
      |   Twilio Media Streams websocket (/ws/twilio-media/{call_id})
      |       |
      |       +-- ElevenLabs STT/TTS service boundary
      |       +-- OpenAI live clearing agent service boundary
      |       +-- Transcript/event persistence
      |
      +-- Post-call extraction agent
      +-- Action router
              |
              +-- MCP Mail customer email action
```

Important design points:

1. **Safe-by-default voice pipeline**: `voice_safe_mode`, `stt_enabled`, `agent_enabled`, and `tts_enabled` default to conservative values so media events can be tested before enabling expensive or live AI/provider calls.
2. **Provider settings stored in the database**: runtime provider credentials are configured through the Settings page and encrypted using `cryptography.fernet`.
3. **Database bootstrap**: tables are created on startup via SQLAlchemy metadata; lightweight additive migrations are included for SQLite and PostgreSQL deployments.
4. **Service boundaries**: Twilio, ElevenLabs, OpenAI, and MCP Mail are isolated in `app/services/` to make provider behavior easy to inspect, mock, or replace.

## Repository structure

```text
app/
  agents/                  Agent wrappers and action routing
  routes/                  FastAPI route modules for UI, webhooks, and websockets
  services/                Provider integrations and domain services
  static/                  CSS assets
  templates/               Jinja2 admin UI templates
  audioop_compat.py        Compatibility helpers for Python audioop behavior
  config.py                Environment-backed application settings
  database.py              SQLAlchemy engine/session/init logic
  main.py                  FastAPI app entrypoint
  models.py                SQLAlchemy ORM models
  security.py              Login/session helpers
  settings_service.py      Encrypted runtime provider settings
tests/
  test_audioop_compat.py   Audio compatibility tests
README.md                  Project documentation
requirements.txt           Python dependencies
render.yaml                Render Blueprint deployment configuration
```

## Technology, APIs, frameworks, and tools

### Application framework

- **FastAPI**: web framework for admin routes, JSON endpoints, Twilio webhooks, and websocket handling.
- **Uvicorn**: ASGI server used locally and on Render.
- **Starlette**: underlying ASGI primitives used by FastAPI for responses, static files, exceptions, and websockets.
- **Jinja2**: server-rendered HTML templates for the dashboard, cases, calls, settings, profiles, and login pages.
- **python-multipart**: form upload support for fixture import and admin forms.

### Data and configuration

- **SQLAlchemy**: ORM, engine, sessions, and schema creation.
- **SQLite**: default local database (`sqlite:///./nomos.db`).
- **PostgreSQL / Neon**: supported production database; `postgres://` and plain `postgresql://` URLs are converted to the `psycopg` SQLAlchemy driver.
- **psycopg[binary]**: PostgreSQL driver.
- **Alembic**: included as a dependency for migration workflows, while this MVP currently uses startup schema creation plus lightweight additive migrations.
- **Pydantic Settings**: reads environment variables and `.env` values.
- **python-dotenv**: `.env` loading support.
- **cryptography / Fernet**: encrypts provider secrets stored in the database.
- **itsdangerous**: available for signed data patterns; current login sessions are backed by database session tokens.
- **passlib[bcrypt]**: available for password hashing workflows; current MVP compares the configured admin username/password from environment variables.

### External APIs and provider SDKs

- **Twilio Voice REST API**: creates outbound calls.
- **Twilio TwiML**: returns `<Connect><Stream>` instructions for media streaming.
- **Twilio Media Streams**: bidirectional websocket path for live call audio and events.
- **ElevenLabs API**: service boundary for STT and TTS model/voice settings.
- **OpenAI API / OpenAI Agents SDK**: service boundary for live clearing-agent replies and post-call extraction.
- **MCP Mail**: configurable HTTP endpoint for sending or dry-running follow-up customer emails.

### Frontend and deployment tools

- **HTMX-compatible responses**: selected routes return partial HTML snippets for dynamic admin UI interactions.
- **Tailwind-style utility classes / local CSS**: styling is contained in templates and `app/static/app.css`.
- **Render Blueprint**: `render.yaml` defines the web service build and start commands.
- **pytest**: recommended test runner for the included tests.

## Prerequisites

- Python 3.11 or newer is recommended.
- `pip` and `venv`.
- Optional provider accounts for live end-to-end demos:
  - Twilio account with a voice-capable phone number.
  - ElevenLabs account/API key for STT/TTS.
  - OpenAI API key for live agent responses and extraction.
  - MCP Mail endpoint/connection details for real customer-email actions.
- For local Twilio Media Streams testing, a publicly reachable HTTPS/WSS URL is required. Use a tunnel such as ngrok or deploy to Render.

## Local setup and installation

### 1. Clone and enter the repository

```bash
git clone <repository-url>
cd Nomos
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create local environment file

Create a `.env` file in the repository root:

```bash
cat > .env <<'EOF_ENV'
DATABASE_URL=sqlite:///./nomos.db
APP_ENCRYPTION_KEY=replace-with-a-long-random-secret-or-fernet-key
APP_BASE_URL=http://localhost:8000
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
SESSION_SECRET_KEY=replace-with-a-long-random-secret
EOF_ENV
```

Generate a Fernet key if you want a standards-compliant encryption key:

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

## Configuration

The app has two layers of configuration.

### Environment variables

These are loaded by `app/config.py` and are required for bootstrapping the app:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | Yes for deployment | `sqlite:///./nomos.db` | SQLAlchemy database URL. SQLite is easiest locally; PostgreSQL/Neon is recommended for deployment. |
| `APP_ENCRYPTION_KEY` | Yes | Empty string | Key material used by `SettingsService` to encrypt provider secrets. Use a stable value; changing it can make existing encrypted settings unreadable. |
| `APP_BASE_URL` | Recommended | `http://localhost:8000` | Public base URL for the app. |
| `ADMIN_USERNAME` | Yes | `admin` | Admin login username. |
| `ADMIN_PASSWORD` | Yes | `admin123` | Admin login password. Change before deployment. |
| `SESSION_SECRET_KEY` | Recommended | `change-me` | Reserved session secret configuration. |

### Runtime provider settings

After logging in, open **Settings** to configure provider-specific values. Secret values are encrypted in the database and displayed as masked values.

Key settings include:

| Setting | Purpose |
| --- | --- |
| `openai_api_key`, `openai_model`, `openai_temperature`, `openai_max_turns` | OpenAI live-agent and extraction configuration. |
| `elevenlabs_api_key`, `elevenlabs_stt_model`, `elevenlabs_tts_model`, voice IDs | ElevenLabs STT/TTS configuration. |
| `twilio_account_sid`, `twilio_auth_token`, `twilio_phone_number`, `twilio_webhook_base_url` | Twilio outbound calling and webhook configuration. |
| `voice_safe_mode`, `stt_enabled`, `agent_enabled`, `tts_enabled` | Feature flags for progressively enabling the live voice pipeline. |
| `default_language`, `outbound_default_language`, `auto_detect_language` | Language behavior for calls and transcripts. |
| `ai_disclosure_required`, `ai_disclosure_de`, `ai_disclosure_en`, `fake_data_only` | Compliance and demo-safety settings. |
| `mcp_server_url`, `mcp_connection_id`, `mcp_sender_email`, `mcp_real_email_enabled` | MCP Mail action configuration. |

## Running the app

Start the development server:

```bash
uvicorn app.main:app --reload
```

Open the app at:

```text
http://localhost:8000
```

Log in with the configured `ADMIN_USERNAME` and `ADMIN_PASSWORD`.

A health endpoint is available at:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

The app initializes database tables during startup.

## Using the admin UI

### Login

Open `/login` and authenticate with the configured admin credentials. Successful login creates an HTTP-only `nomos_session` cookie.

### Configure providers

Open **Settings** and enter OpenAI, ElevenLabs, Twilio, compliance, language, voice, and MCP Mail values. Use each provider section's **Test connection** button to verify credentials without revealing stored secrets.

### Create profiles

Use **Profiles** to save reusable customer/grid-operator metadata such as customer name, address, meter number, market-location number, target phone number, and preferred language.

### Create cases

Use **Cases → Create Case** to enter clearing metadata, select a scenario, optionally link a profile, and set target phone/language fields. Scenario defaults prefill the case type, problem description, and required outcome when those fields are left blank.

### Import fixtures

Open **Import Fixtures** and upload either:

```json
[
  {
    "external_case_id": "CASE-001",
    "scenario": "correct_malo_id",
    "customer_name": "Demo Customer",
    "customer_address": "Musterstraße 12, Mainz-Kastel",
    "target_phone_number": "+491234567890"
  }
]
```

or:

```json
{
  "cases": [
    {
      "external_case_id": "CASE-002",
      "scenario": "meter_status_clarification"
    }
  ]
}
```

### Start outbound calls

From the case list or detail page, click **Start outbound call**. The app creates a `Call`, invokes Twilio, sets the Twilio voice URL to `/twilio/voice/{call_id}`, and updates the case status to `calling` if Twilio accepts the call.

### Review calls, transcripts, and extraction

Open **Calls**, choose a call, and inspect:

- call metadata and status,
- media/debug counters,
- persisted events,
- transcripts,
- structured extraction rows,
- action history.

Use **Rerun extraction** to regenerate structured extraction from saved call artifacts.

### Trigger follow-up actions

If extraction returns `next_action = trigger_customer_email_agent`, use **Trigger next action manually** or allow the post-call router to run. With `mcp_real_email_enabled=false`, MCP Mail runs in dry-run mode and records the action without sending a real email.

## HTTP and websocket API reference

The app is primarily server-rendered, but these routes are important for evaluation and integration.

### Authentication and UI routes

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| `GET` | `/login` | Render login page. | No |
| `POST` | `/login` | Create admin session cookie. | No |
| `POST` | `/logout` | Delete admin session and redirect to login. | Cookie |
| `GET` | `/` | Dashboard statistics. | Cookie |
| `HEAD` | `/` | Lightweight root check. | No |
| `GET` | `/health` | JSON health check. | No |

### Case and profile routes

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| `GET` | `/profiles` | List profiles. | Cookie |
| `GET` | `/profiles/new` | Render profile creation form. | Cookie |
| `POST` | `/profiles` | Create profile. | Cookie |
| `GET` | `/profiles/{profile_id}/edit` | Render profile edit form. | Cookie |
| `POST` | `/profiles/{profile_id}` | Update profile. | Cookie |
| `POST` | `/profiles/{profile_id}/delete` | Delete profile. | Cookie |
| `GET` | `/cases` | List/filter cases. Returns a table partial for HTMX requests. | Cookie |
| `GET` | `/cases/new` | Render case creation form. | Cookie |
| `POST` | `/cases` | Create a case and optionally start a call. | Cookie |
| `GET` | `/cases/import-fixtures` | Render fixture import form. | Cookie |
| `POST` | `/cases/import-fixtures` | Import fixture JSON. | Cookie |
| `GET` | `/cases/{case_id}` | Case detail with calls/extraction/action summary. | Cookie |
| `POST` | `/cases/{case_id}/status` | Update case status. | Cookie |

### Call and action routes

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| `GET` | `/calls` | List call history. | Cookie |
| `GET` | `/calls/{call_id}` | Call detail, transcripts, events, extraction, and debug summary. | Cookie |
| `POST` | `/calls/outbound` | Start outbound call from form data containing `case_id`. | Cookie |
| `POST` | `/api/calls/outbound` | API alias for starting outbound call. | Cookie |
| `POST` | `/calls/{call_id}/debug-send-greeting` | Send test greeting to an active websocket call session. | Cookie |
| `POST` | `/calls/{call_id}/debug-send-short-test-reply` | Send a short test TTS reply to an active websocket call session. | Cookie |
| `POST` | `/calls/{call_id}/extract` | Run post-call extraction for a call. | Cookie |
| `POST` | `/calls/{call_id}/trigger-action` | Run the next action from latest extraction. | Cookie |

### Settings routes

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| `GET` | `/settings` | Render settings page with masked secrets. | Cookie |
| `POST` | `/settings` | Save runtime settings. | Cookie |
| `POST` | `/settings/test/{provider}` | Test provider connection. Providers include OpenAI, ElevenLabs, Twilio, and MCP Mail. | Cookie |

### Twilio routes

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| `POST` | `/twilio/voice/{call_id}` | Twilio voice webhook. Returns TwiML that connects the call to `/ws/twilio-media/{call_id}`. | Twilio-originated |
| `POST` | `/twilio/status` | Twilio status callback. Updates call status, timestamps, duration, recording URL, and events. | Twilio-originated |

### Websocket routes

| Protocol | Path | Purpose |
| --- | --- | --- |
| `WS/WSS` | `/ws/twilio-media/{call_id}` | Receives Twilio Media Streams events, persists media/debug events, optionally runs STT, agent, TTS, and extraction/action routing. |

## Voice pipeline details

### Safe-mode validation path

Use this path before enabling live AI processing:

1. In **Settings**, set:
   - `voice_safe_mode=true`
   - `stt_enabled=false`
   - `agent_enabled=false`
   - `tts_enabled=false`
2. Start an outbound call.
3. Speak for at least 10 seconds after the call connects.
4. Confirm the call does not drop because of backend exceptions.
5. Confirm logs show `twilio_media_event` entries.
6. Confirm the Call Detail page shows increasing media event counts.

### Progressive enablement path

After safe mode is stable:

1. Set `voice_safe_mode=false` and `stt_enabled=true`.
2. Verify final STT transcript rows are saved.
3. Set `agent_enabled=true` after STT is stable.
4. Verify `agent_completed` events and transcript/response behavior.
5. Set `tts_enabled=true` after agent responses are stable.
6. Verify outbound audio queue behavior, TTS chunks, and barge-in/backpressure metrics.

### Audio handling

Twilio Media Streams use 8 kHz µ-law audio. The app contains conversion helpers for:

- Twilio µ-law to 16 kHz PCM WAV for STT boundaries.
- Provider PCM/µ-law output to Twilio-compatible µ-law where needed.

## Data model

Primary tables:

| Table | Purpose |
| --- | --- |
| `app_settings` | Runtime provider and feature-flag settings, including encrypted secrets. |
| `admin_sessions` | HTTP-only admin session tokens and expiry timestamps. |
| `profiles` | Reusable customer and grid-operator data. |
| `cases` | Clearing cases, scenario metadata, contact details, statuses, and outcomes. |
| `calls` | Outbound call records and Twilio metadata. |
| `call_state` | Live call state machine fields such as phase, language, known/corrected numbers, meter status, and hold mode. |
| `call_transcripts` | User/agent transcript rows with language, confidence, timestamp, and source. |
| `call_events` | Structured debug/provider/media events. |
| `call_extractions` | Structured post-call outcome and next-action data. |
| `action_runs` | Records for routed follow-up actions. |

## Testing and validation

Run the included test suite:

```bash
pytest
```

If pytest is not installed in your environment, install it in the active virtual environment:

```bash
pip install pytest
```

Recommended manual checks for jury evaluation:

1. Start the app locally with SQLite.
2. Log in with admin credentials.
3. Create a profile and a case.
4. Import a small fixture JSON file.
5. Open Settings and verify default safe-mode flags.
6. Use `/health` to verify the app is alive.
7. If provider credentials are available, test Twilio/OpenAI/ElevenLabs/MCP Mail from Settings.
8. For live voice demos, expose the app through HTTPS/WSS and set `twilio_webhook_base_url` to that public URL.

## Deployment

### Render Blueprint

`render.yaml` defines a Python web service:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Deploy steps:

1. Push this repository to GitHub.
2. In Render, create a Blueprint from `render.yaml` or create a Python Web Service manually.
3. Set required environment variables:
   - `DATABASE_URL`
   - `APP_ENCRYPTION_KEY`
   - `APP_BASE_URL`
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD`
   - `SESSION_SECRET_KEY`
4. Configure a Neon/PostgreSQL connection string for persistent production data.
5. Set `twilio_webhook_base_url` in the app Settings page to the public Render service URL.

### Neon PostgreSQL

1. Create a Neon project.
2. Copy the pooled PostgreSQL connection string.
3. Use it as `DATABASE_URL`.
4. The app automatically converts plain `postgres://` or `postgresql://` URLs to the `postgresql+psycopg://` SQLAlchemy driver form.

## Security and compliance notes

- Change `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET_KEY`, and `APP_ENCRYPTION_KEY` before deployment.
- Keep `APP_ENCRYPTION_KEY` stable across deploys; it is needed to decrypt stored provider settings.
- Provider secrets are stored encrypted and are masked in the UI.
- Admin pages require the `nomos_session` HTTP-only cookie.
- `ai_disclosure_required` and language-specific disclosure text are included to support transparent AI calling flows.
- `fake_data_only=true` is the default intended for demos and jury review.
- `mcp_real_email_enabled=false` keeps customer-email actions in dry-run mode by default.
- For production telephony, confirm consent, disclosure, recording, data retention, and jurisdiction-specific calling rules before use.

## Troubleshooting

### Login redirects back to `/login`

- Confirm `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env` match the credentials entered.
- Restart the app after changing `.env` values.

### Database errors on startup

- For SQLite, ensure the process can write to the repository directory.
- For PostgreSQL/Neon, confirm `DATABASE_URL` is reachable and contains the correct password/SSL settings.

### Twilio call starts but websocket does not connect

- `twilio_webhook_base_url` must be public and HTTPS.
- Websocket connections must use `wss://` externally; the Twilio voice route constructs the stream URL from the configured webhook base URL.
- Confirm the deployment allows websocket traffic.

### No transcripts appear

- Confirm `voice_safe_mode=false` and `stt_enabled=true`.
- Confirm ElevenLabs credentials and STT model settings are saved.
- Check Call Detail events for `stt_invalid_transcript`, `websocket_error`, or provider errors.

### Agent replies do not appear

- Confirm `agent_enabled=true`.
- Confirm OpenAI credentials and model settings are saved.
- Check Call Detail events for `agent_completed` or error events.

### TTS audio does not play

- Confirm `tts_enabled=true`.
- Confirm ElevenLabs voice IDs and TTS model settings are saved.
- Review Call Detail debug counters for queue backpressure, skipped responses, and sent TTS chunks.

## License

No license file is currently included. Treat the repository as proprietary unless a license is added by the project owner.

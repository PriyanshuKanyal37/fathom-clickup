# Fathom → ClickUp

Backend service that turns every Fathom meeting into a ClickUp task — automatically.

When a team member ends a Fathom-recorded meeting, a webhook fires into this service, which:

1. Verifies Fathom's HMAC signature
2. Dedupes against prior deliveries (race-safe via Postgres primary key)
3. Uses Fathom's AI summary + action items when available, or falls back to an OpenAI LLM (Fathom-style structure: Meeting Purpose / Key Takeaways / Topics / Next Steps)
4. Generates a meaningful meeting title with the LLM when Fathom gives a generic one ("Impromptu Google Meet Meeting" → "ClickUp Brain AI Project Onboarding")
5. Creates a ClickUp parent task with an `FM-N` prefix (title, dates in IST, description, assignees matched from attendees + transcript speakers) plus checklist items for action items

## Stack

FastAPI (async) · SQLModel/SQLAlchemy · Postgres (Neon) · OpenAI Responses API · httpx · Fernet · Uvicorn

## Layout

```
backend/
├── app/          application code
├── tests/        pytest suite
├── Dockerfile
├── requirements.txt
└── .env.example  template of required environment variables
```

## Run locally

```bash
cd backend
cp .env.example .env   # fill in real values
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

No frontend — all operations are CLI via admin HTTP endpoints (protected by an `X-Admin-Token` header):

- `POST /admin/users` — add a user + register their Fathom webhook
- `GET  /admin/users` — list active users
- `DELETE /admin/users/{id}` — remove user + deregister webhook
- `POST /admin/users/{id}/rotate-key` — rotate Fathom/ClickUp keys
- `GET  /admin/meetings?status=success|failed|processing` — inspect DB state
- `POST /admin/meetings/{recording_id}/retry` — replay a stored payload
- `GET  /admin/health` — health check

## Webhook endpoint

`POST /webhooks/fathom/{user_id}` — registered per-user, signed by Fathom.

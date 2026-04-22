# KnownSense Enterprise Sandbox Python Starter

This project is the official local starter for integrating with the KnownSense Enterprise Analysis API.

It is intended for external developer teams who need to validate their integration before switching to production credentials. The starter focuses on the parts of the integration that usually fail first:

- authenticated `POST /api/v1/analysis/jobs`
- idempotent retries
- webhook signature verification
- local callback testing through `ngrok`
- deterministic hosted-sandbox fixtures
- polling and webhook-based completion handling
- full smoke coverage for `completed` and `refunded` terminal states

Use this project before writing your own production client.

## Why This Starter Exists

This repo is intentionally small. It is not a full SDK and it is not a generated client.

It exists to give external teams a known-good reference implementation for the parts of the integration that usually break first:

- authenticated async job creation
- idempotency handling
- webhook signature verification
- webhook delivery debugging through a public tunnel
- polling fallback when a webhook is unavailable

If your team can run this starter end to end against the KnownSense sandbox, you can usually port the same logic into your own stack with much less risk.

## What You Need Before You Start

Before starting, make sure you have all of the following:

1. A provisioned **sandbox workspace** in the KnownSense dashboard.
2. A **sandbox API key** generated while that sandbox workspace is active.
3. A **sandbox webhook endpoint** configured in dashboard settings.
4. The **webhook signing secret** returned when the webhook is created or rotated.
5. A public HTTPS tunnel URL from `ngrok` or `cloudflared`.

Important:

- Sandbox and production use the same host: `https://audio.knownsense.ai`
- Sandbox is selected by the API key, not by a different hostname
- Sandbox and production webhooks are fully separate

## What This Starter Validates

If this starter passes end to end, your team has high confidence in:

- API key authentication
- request formatting
- idempotent create-job behavior
- polling behavior
- webhook delivery and signature verification
- callback payload parsing
- sandbox-only deterministic fixture execution
- handling both successful and refunded terminal paths

It does **not** test live Gemini quality or production microphone data. That is expected. The hosted sandbox is a deterministic integration environment, not a model evaluation environment.

## Project Files

- `receiver.py`
  - local Flask webhook receiver
  - verifies `X-Kenso-*` headers and HMAC signature
  - appends received events to `data/received_events.jsonl`
- `sandbox_client.py`
  - CLI helper for listing templates, fixtures, jobs, creating jobs, polling, waiting on terminal webhooks, and running the full smoke suite
- `.env.example`
  - environment variables to copy into `.env`
- `requirements.txt`
  - Python dependencies

## Setup

```bash
cd examples/enterprise_sandbox_python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Both scripts auto-load `.env` from this folder. You do not need to `export` it manually.
Relative paths like `./data/received_events.jsonl` are resolved from this project directory, even if you launch the scripts from elsewhere.

Config precedence:

- by default, this starter prefers the repo `.env`
- if a stale `KENSO_*` shell export disagrees with `.env`, the script prints a warning and still uses `.env`
- if you intentionally want shell values to override `.env`, set `KENSO_PREFER_PROCESS_ENV=true`

## Required .env Values

Use these as the minimum local values:

```bash
KENSO_API_BASE=https://audio.knownsense.ai
KENSO_API_KEY=ks_sandbox_...
KENSO_WEBHOOK_SECRET=whsec_...
KENSO_WEBHOOK_EVENT_LOG=./data/received_events.jsonl
KENSO_WEBHOOK_HOST=127.0.0.1
KENSO_WEBHOOK_PORT=8787
KENSO_WEBHOOK_PATH=/webhooks/knownsense
```

Field meanings:

- `KENSO_API_BASE`
  - hosted sandbox and production host: `https://audio.knownsense.ai`
  - only override it if you are intentionally testing against a developer backend
- `KENSO_API_KEY`
  - sandbox API key from the dashboard
- `KENSO_PREFER_PROCESS_ENV`
  - optional override if you intentionally want shell-exported `KENSO_*` values to beat `.env`
- `KENSO_WEBHOOK_SECRET`
  - signing secret returned when you create or rotate the sandbox webhook
- `KENSO_WEBHOOK_EVENT_LOG`
  - local file the smoke runner reads when validating webhook deliveries

## Webhook Constraint

KnownSense intentionally blocks direct webhook targets like:

- `localhost`
- `.localhost`
- private/local IP addresses

So the correct local webhook flow is:

1. run `receiver.py` locally
2. expose it through `ngrok` or `cloudflared`
3. register the public HTTPS URL in dashboard settings
4. create sandbox jobs
5. validate the terminal webhook events locally

## Start The Receiver

```bash
source .venv/bin/activate
python receiver.py
```

Default local URL:

```text
http://127.0.0.1:8787/webhooks/knownsense
```

Health endpoint:

```text
http://127.0.0.1:8787/health
```

## Expose The Receiver With ngrok

If this is your first time using `ngrok`, add your auth token once:

```bash
ngrok config add-authtoken <your-ngrok-authtoken>
```

Then start the tunnel:

```bash
ngrok http 8787
```

Copy the HTTPS forwarding URL and set the sandbox webhook endpoint in dashboard settings as:

```text
https://<your-ngrok-host>/webhooks/knownsense
```

## Create The Sandbox Webhook

In `Dashboard -> Settings`:

1. switch to the **Sandbox workspace**
2. open the **Webhooks** section
3. set the endpoint to the current `ngrok` URL plus `/webhooks/knownsense`
4. save the webhook
5. copy the returned `whsec_*` signing secret immediately
6. paste that secret into `.env`

If you lose the secret, rotate it and update `.env`.

## First Callback Test

Once the receiver and webhook are configured:

1. keep `receiver.py` running
2. click **Send Test** in dashboard settings
3. confirm the receiver prints a verified `webhook.test` event
4. confirm `data/received_events.jsonl` contains the payload

If the dashboard shows `502 Bad Gateway`, the tunnel could not reach your local receiver. Check:

- `receiver.py` is still running
- `ngrok` is still running
- the dashboard webhook points to the current `ngrok` URL
- the local receiver is still bound to port `8787`

## Discover Sandbox Fixtures

```bash
python sandbox_client.py list-fixtures
```

If you want to confirm exactly which key the script is using before you hit the API:

```bash
python sandbox_client.py show-config
```

That prints the resolved config source for each important value, including whether the current API key came from `.env` or from the shell.

This returns the deterministic fixture windows and the expected terminal states. Use these instead of guessing sandbox time ranges.

## List Public Templates

```bash
python sandbox_client.py list-templates
```

This confirms the API key works and gives your client the current public template contract.

## Auth Troubleshooting

Use this quick diagnostic order before attempting job creation:

```bash
python sandbox_client.py show-config
python sandbox_client.py list-fixtures
```

Interpret the result like this:

- `200` from `list-fixtures`
  - your sandbox key is valid
- `404 not_found/endpoint`
  - the key is valid, but it is a production key, not a sandbox key
- `401 auth/invalid_api_key`
  - the key is wrong, revoked, malformed, or from another workspace/company

Because `create-from-fixture` first calls `GET /api/v1/analysis/sandbox/fixtures`, you should always debug authentication with `list-fixtures` first.

## Recommended Test Flows

### 1. Webhook-first single-job success test

This is the best first proof that your integration is wired correctly:

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id generic_success \
  --avoid-cache \
  --wait-webhook \
  --expected-event-type job.completed
```

What it proves:

- create-job request works
- a fresh sandbox job was created
- terminal webhook delivery reached your receiver
- your signature verification is correct

### 2. Polling-based single-job success test

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id generic_success \
  --avoid-cache \
  --poll
```

Use this when validating operator workflows or fallback behavior where a webhook is unavailable.

### 3. Verification workflow test

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id verification_success \
  --avoid-cache \
  --wait-webhook \
  --expected-event-type job.completed
```

This proves the verification-oriented template path and hybrid result contract.

### 4. Refunded terminal-state test

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id verification_refunded \
  --avoid-cache \
  --wait-webhook \
  --expected-event-type job.refunded
```

This is the most important negative-path test in the sandbox.

### 5. Full smoke suite

```bash
python sandbox_client.py run-smoke-suite \
  --verify-webhooks \
  --webhook-timeout-seconds 120
```

The smoke suite covers:

- `generic_success` -> `job.completed`
- `verification_success` -> `job.completed`
- `verification_refunded` -> `job.refunded`

The suite validates:

- expected templates exist
- fresh jobs are created
- terminal state matches the fixture contract
- `result_kind` and `schema_version` are correct
- `external_reference_id` is echoed
- recent job listing works
- webhook events arrive in the local receiver log

## Useful Commands

List fixtures:

```bash
python sandbox_client.py list-fixtures
```

List templates:

```bash
python sandbox_client.py list-templates
```

List jobs:

```bash
python sandbox_client.py list-jobs --limit 10
```

Fetch one job:

```bash
python sandbox_client.py get-job --job-id job_...
```

Poll one job:

```bash
python sandbox_client.py poll-job --job-id job_...
```

## What A Passing Integration Looks Like

You should consider the integration sandbox-ready when all of the following are true:

- `webhook.test` is delivered and verified
- `generic_success` reaches `job.completed`
- `verification_success` reaches `job.completed`
- `verification_refunded` reaches `job.refunded`
- terminal webhooks are received for those jobs
- the client can parse the returned `result`
- successful jobs return `audio_artifact.status = ready`

At that point, moving to production is mostly:

- switching from sandbox API key to production API key
- replacing fixture windows with real microphone/time-window inputs
- keeping the same request and webhook handling code

## What To Share With External Developers

When you hand this starter to an integration team, they should only need:

- this repo
- a sandbox API key
- a sandbox webhook secret
- one agreed webhook test flow
- one agreed smoke-suite pass before production credentials

That keeps the onboarding surface small and avoids premature SDK work before webhook delivery is proven.

## Troubleshooting

### Job stays `pending` for a while

That is normal. The API is asynchronous. The hosted sandbox still moves jobs through the real create -> process -> terminal lifecycle.

### The same old job ID keeps coming back

That is usually a request-fingerprint cache hit. Use:

```bash
--avoid-cache
```

when repeating local tests.

### Webhook shows `502 Bad Gateway`

That means the tunnel or local receiver was unavailable, not that the sandbox job failed.

Check:

- `receiver.py` is running
- `ngrok` is running
- the dashboard uses the current tunnel URL
- the local receiver path is `/webhooks/knownsense`

### The webhook secret no longer works

Rotate the secret in the dashboard, update `.env`, and restart `receiver.py`.

## Environment Variable Reference

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `KENSO_API_BASE` | No | `http://localhost:9090` | KnownSense API base URL |
| `KENSO_API_KEY` | Yes | — | Sandbox API key |
| `KENSO_WEBHOOK_SECRET` | Yes for receiver | — | Sandbox webhook signing secret |
| `KENSO_WEBHOOK_EVENT_LOG` | No | `./data/received_events.jsonl` | Local event log for webhook verification |
| `KENSO_WEBHOOK_PATH` | No | `/webhooks/knownsense` | Receiver route path |
| `KENSO_WEBHOOK_HOST` | No | `127.0.0.1` | Receiver bind host |
| `KENSO_WEBHOOK_PORT` | No | `8787` | Receiver bind port |
| `KENSO_WEBHOOK_MAX_SKEW_SECONDS` | No | `300` | Allowed timestamp skew for signature verification |

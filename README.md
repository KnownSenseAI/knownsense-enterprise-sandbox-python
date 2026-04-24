# KnownSense Enterprise Sandbox Starter

Use this starter to validate your integration against the KnownSense hosted sandbox before you switch to production credentials.

Standalone repo:

`https://github.com/KnownSenseAI/knownsense-enterprise-sandbox-python`

What this starter proves:

- sandbox API key auth
- fixture discovery
- async job creation
- polling
- webhook delivery
- webhook signature verification
- completed and refunded terminal states
- optional real-audio sandbox upload through the live Gemini backend

What it does **not** prove:

- live production audio quality
- customer-specific business logic in your own app

Optional real-audio sandbox testing does exercise Gemini on your uploaded file.
It still keeps sandbox job/webhook isolation, but it consumes the root production
company's analysis credits.

This repo includes a small sample audio file:

```text
samples/bus_station_test.m4a
```

An example completed response for that file is available at:

```text
examples/live_audio_completed_response.json
```

## Before You Start

You need:

1. A provisioned **sandbox workspace** in the KnownSense dashboard.
2. A **sandbox API key** generated while that sandbox workspace is active.
3. A **sandbox webhook** created in that same sandbox workspace.
4. The webhook signing secret `whsec_*`.
5. A public HTTPS tunnel from `ngrok` or `cloudflared`.

Important:

- sandbox and production use the same host: `https://audio.knownsense.ai`
- sandbox is selected by the API key, not by a different hostname
- sandbox webhooks and production webhooks are separate

## Setup

Use either the standalone repo or the monorepo mirror.

```bash
# standalone repo
git clone https://github.com/KnownSenseAI/knownsense-enterprise-sandbox-python.git
cd knownsense-enterprise-sandbox-python

# monorepo mirror
# cd examples/enterprise_sandbox_python

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Minimum `.env`:

```bash
KENSO_API_BASE=https://audio.knownsense.ai
KENSO_API_KEY=ks_sandbox_...
KENSO_WEBHOOK_SECRET=whsec_...
KENSO_WEBHOOK_EVENT_LOG=./data/received_events.jsonl
KENSO_WEBHOOK_HOST=127.0.0.1
KENSO_WEBHOOK_PORT=8787
KENSO_WEBHOOK_PATH=/webhooks/knownsense
```

Config behavior:

- this starter prefers the local `.env` by default
- stale exported `KENSO_*` shell vars do **not** silently override `.env`
- if you intentionally want shell values to win, set `KENSO_PREFER_PROCESS_ENV=true`

## Start The Receiver

```bash
source .venv/bin/activate
python receiver.py
```

Default local receiver URL:

```text
http://127.0.0.1:8787/webhooks/knownsense
```

Expose it:

```bash
ngrok http 8787
```

Then configure the sandbox webhook in dashboard settings as:

```text
https://<your-ngrok-host>/webhooks/knownsense
```

## Run Tests In This Order

Do not start with the smoke suite. Run the short checks first.

### 1. Confirm local config

```bash
python sandbox_client.py show-config
```

What to check:

- `api_base.source` should normally be `.env`
- `api_key.source` should normally be `.env`
- the masked key prefix should match the key you just generated

### 2. Confirm sandbox auth

```bash
python sandbox_client.py list-fixtures
```

Interpret the result:

- `200`: sandbox key is valid
- `404 not_found/endpoint`: key is valid, but it is a **production** key
- `401 auth/invalid_api_key`: key is wrong, revoked, malformed, or there is a backend auth issue
- `403 auth/api_disabled`: key is valid, but API access is disabled for that workspace

Always fix `list-fixtures` before trying job creation.

### 3. Optional: test real audio through Gemini

Use this when you want to validate the real Gemini analysis path without deploying hardware.

Upload only:

```bash
python sandbox_client.py upload-live-audio \
  --file ./samples/bus_station_test.m4a \
  --mic-name "Bus Station Test Audio"
```

The response includes a sandbox `mic_id`, `time_range_start_unix`, and `time_range_end_unix`.
Use them within 1 hour. The uploaded live-audio window is one-time-use.

Upload and immediately create the analysis job:

```bash
python sandbox_client.py create-from-live-audio \
  --file ./samples/bus_station_test.m4a \
  --template-id generic.analysis.v1 \
  --poll
```

With webhook verification:

```bash
python sandbox_client.py create-from-live-audio \
  --file ./samples/bus_station_test.m4a \
  --template-id generic.analysis.v1 \
  --wait-webhook \
  --poll
```

If job creation fails after upload, the command prints the uploaded window first.
You can retry manually with that returned `mic_id` and time range until it expires.

Expected shape for the included sample:

- `status` becomes `completed`
- `estimated_credits` and `actual_credits` are normally `2`
- `result.core_analysis` contains findings, highlights, recommendations, and summary
- `audio_artifact.status` becomes `ready`
- `audio_artifact.download_url` is a short-lived signed URL; do not store it permanently
- full example: `examples/live_audio_completed_response.json`

### 4. Confirm webhook reachability from the dashboard

In dashboard settings, click **Send Test**.

Expected result:

- receiver logs a verified `webhook.test`
- `data/received_events.jsonl` gets a new event

This only proves webhook delivery reachability. It does **not** prove analysis job creation yet.

### 5. Happy-path webhook flow

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id generic_success \
  --avoid-cache \
  --wait-webhook \
  --expected-event-type job.completed
```

This proves:

- authenticated job creation
- sandbox fixture matching
- async processing
- terminal `job.completed` webhook delivery

### 6. Refunded terminal flow

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id verification_refunded \
  --avoid-cache \
  --wait-webhook \
  --expected-event-type job.refunded
```

This proves your integration handles a terminal refunded path, not only success.

### 7. Polling-only flow

```bash
python sandbox_client.py create-from-fixture \
  --fixture-id generic_success \
  --avoid-cache \
  --poll
```

This proves your fallback polling path works even if webhooks are unavailable.

### 8. Full smoke suite

```bash
python sandbox_client.py run-smoke-suite --verify-webhooks
```

Run this last, after the smaller checks above pass.

## Common Issues

### `show-config` says the key comes from `process_env`

You still have an exported shell variable overriding local expectations.

Fix:

- remove the stale export, or
- keep using `.env`, or
- intentionally set `KENSO_PREFER_PROCESS_ENV=true`

### Dashboard **Send Test** works, but Python calls fail

Those are different auth paths:

- dashboard test uses your logged-in dashboard session
- Python client uses `X-API-Key`

So webhook reachability can be fine while API auth is still wrong.

### `list-fixtures` returns `404 not_found/endpoint`

Your key is real, but it is a production key, not a sandbox key.

Fix:

- switch to the sandbox workspace
- generate a fresh sandbox API key there
- update `.env`

### `list-fixtures` returns `401 auth/invalid_api_key`

Most common causes:

- wrong key pasted into `.env`
- revoked key
- key from another workspace or company
- backend auth infrastructure problem

Fix:

1. rotate or regenerate the sandbox key
2. rerun `python sandbox_client.py show-config`
3. rerun `python sandbox_client.py list-fixtures`
4. if it still fails, share the returned `request_id` with KnownSense support

### `list-fixtures` returns `403 auth/api_disabled`

Your key is valid, but API access is disabled on that workspace.

Fix:

1. enable API access in the dashboard, or ask KnownSense support to enable it
2. generate a fresh sandbox API key if the old key was revoked
3. rerun `python sandbox_client.py list-fixtures`

### `create-from-fixture` fails before creating a job

`create-from-fixture` first loads sandbox fixtures. If auth or fixture discovery is broken, job creation never starts.

Fix `list-fixtures` first.

### `create-from-live-audio` returns `402 billing/insufficient_credits`

Live-audio sandbox tests use the real Gemini backend and consume the root production company's analysis credits.

Fix:

- use the deterministic fixture flow for free contract testing, or
- add production analysis credits before testing real audio

### `create-from-live-audio` says the upload is expired or already used

Live-audio sandbox uploads are valid for 1 hour and are one-time-use.

Fix:

- upload the file again
- use the newly returned `mic_id` and time range

### `--wait-webhook` times out

The job may still have completed. This usually means webhook delivery failed or your receiver/tunnel was unavailable.

Check:

- `receiver.py` is still running
- `ngrok` is still running
- the webhook URL in dashboard settings matches the current tunnel
- dashboard delivery history for that webhook
- polling result with `get-job` or `poll-job`

### Dashboard says webhook is `queued` but nothing arrives

First check:

- receiver is running
- tunnel is live
- the saved webhook URL is correct

If those are correct and deliveries remain queued, collect the delivery record and contact KnownSense. That usually indicates a backend delivery issue, not a client bug.

### Repeated tests reuse old results

Use `--avoid-cache` on repeated sandbox runs.

### You guessed timestamps manually

Do not guess sandbox time ranges. Use:

```bash
python sandbox_client.py list-fixtures
```

and copy the seeded fixture values exactly.

## Useful Commands

```bash
python sandbox_client.py show-config
python sandbox_client.py list-fixtures
python sandbox_client.py list-templates
python sandbox_client.py upload-live-audio --file ./samples/bus_station_test.m4a
python sandbox_client.py create-from-live-audio --file ./samples/bus_station_test.m4a --poll
python sandbox_client.py list-jobs --limit 10
python sandbox_client.py get-job --job-id job_...
python sandbox_client.py poll-job --job-id job_...
```

## Minimum Signoff

Treat the integration as sandbox-ready when all of these pass:

- `show-config`
- `list-fixtures`
- dashboard `Send Test`
- one `job.completed` run
- one `job.refunded` run
- one polling-only run
- full smoke suite

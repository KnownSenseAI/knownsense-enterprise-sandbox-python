#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WEBHOOK_LOG = SCRIPT_DIR / "data" / "received_events.jsonl"
TERMINAL_STATUSES = {"completed", "failed", "refunded"}
TRUE_VALUES = {"1", "true", "yes", "on"}

def load_dotenv_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip("\"'")
    return values


DOTENV_VALUES = load_dotenv_file(SCRIPT_DIR / ".env")
CONFIG_SOURCES: dict[str, str] = {}
PREFER_PROCESS_ENV = os.getenv("KENSO_PREFER_PROCESS_ENV", "").strip().lower() in TRUE_VALUES


def env(name: str, default: str = "") -> str:
    process_value = os.getenv(name)
    dotenv_value = DOTENV_VALUES.get(name)

    if dotenv_value is not None and (process_value is None or not PREFER_PROCESS_ENV):
        CONFIG_SOURCES[name] = ".env"
        if process_value is not None and process_value.strip() != dotenv_value.strip():
            print(
                (
                    f"[config] {name}: using .env value {mask_secret(dotenv_value)} "
                    f"instead of process env {mask_secret(process_value)}. "
                    "Set KENSO_PREFER_PROCESS_ENV=true to force shell overrides."
                ),
                file=sys.stderr,
            )
        return dotenv_value.strip()

    if process_value is not None:
        CONFIG_SOURCES[name] = "process_env"
        return process_value.strip()

    CONFIG_SOURCES[name] = "default"
    return default.strip()


def mask_secret(value: str, *, head: int = 12) -> str:
    value = value.strip()
    if not value:
        return "<empty>"
    if len(value) <= head:
        return value
    return value[:head] + "..."


def resolve_local_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


API_BASE = env("KENSO_API_BASE", "https://audio.knownsense.ai").rstrip("/")
API_KEY = env("KENSO_API_KEY")
WEBHOOK_EVENT_LOG = resolve_local_path(env("KENSO_WEBHOOK_EVENT_LOG", "./data/received_events.jsonl"))
ANALYSIS_BASE = f"{API_BASE}/api/v1/analysis"


def require_api_key() -> str:
    if not API_KEY:
        raise SystemExit("KENSO_API_KEY is required")
    return API_KEY


def config_value_source(name: str) -> str:
    return CONFIG_SOURCES.get(name, "unknown")


def config_snapshot() -> dict[str, Any]:
    return {
        "api_base": {
            "value": API_BASE,
            "source": config_value_source("KENSO_API_BASE"),
        },
        "api_key": {
            "value": mask_secret(API_KEY),
            "source": config_value_source("KENSO_API_KEY"),
        },
        "webhook_secret": {
            "value": mask_secret(env("KENSO_WEBHOOK_SECRET")),
            "source": config_value_source("KENSO_WEBHOOK_SECRET"),
        },
        "webhook_event_log": {
            "value": str(WEBHOOK_EVENT_LOG),
            "source": config_value_source("KENSO_WEBHOOK_EVENT_LOG"),
        },
        "prefer_process_env": PREFER_PROCESS_ENV,
    }


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        "X-API-Key": require_api_key(),
    }
    if extra:
        base.update(extra)
    return base


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=True)


def request_json(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    url = f"{ANALYSIS_BASE}{path}"
    req_headers = headers(extra_headers)
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    response = requests.request(method, url, headers=req_headers, json=body, timeout=30)
    if not response.ok:
        raise RuntimeError(f"{response.status_code} {response.text}")
    if not response.text:
        return None
    return response.json()


def request_multipart(
    path: str,
    *,
    file_path: Path,
    form_fields: dict[str, str] | None = None,
) -> Any:
    url = f"{ANALYSIS_BASE}{path}"
    req_headers = headers()
    with file_path.open("rb") as fh:
        response = requests.post(
            url,
            headers=req_headers,
            files={"file": (file_path.name, fh)},
            data=form_fields or {},
            timeout=120,
        )
    if not response.ok:
        raise RuntimeError(f"{response.status_code} {response.text}")
    if not response.text:
        return None
    return response.json()


def list_templates() -> list[dict[str, Any]]:
    data = request_json("GET", "/templates")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected template response")
    return data


def list_fixtures() -> list[dict[str, Any]]:
    data = request_json("GET", "/sandbox/fixtures")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected fixture response")
    return data


def list_jobs(*, limit: int = 20, status: str = "") -> list[dict[str, Any]]:
    query: dict[str, Any] = {"limit": limit}
    if status:
        query["status"] = status
    encoded = urlencode(query)
    data = request_json("GET", f"/jobs?{encoded}")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected jobs response")
    return data


def get_fixture_by_id(fixture_id: str) -> dict[str, Any]:
    for fixture in list_fixtures():
        if fixture.get("fixture_id") == fixture_id:
            return fixture
    raise RuntimeError(f"Fixture not found: {fixture_id}")


def build_fixture_payload(
    fixture: dict[str, Any],
    *,
    instructions: str = "",
    reference_data: dict[str, Any] | None = None,
    checks: list[dict[str, Any]] | None = None,
    external_reference_id: str = "",
    free_text_notes: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": fixture["template_id"],
        "mic_ids": [fixture["mic_id"]],
        "time_range_start_unix": fixture["time_range_start_unix"],
        "time_range_end_unix": fixture["time_range_end_unix"],
    }
    if instructions:
        payload["instructions"] = instructions
    if reference_data:
        payload["reference_data"] = reference_data
    if checks:
        payload["checks"] = checks
    if external_reference_id:
        payload["external_reference_id"] = external_reference_id
    if free_text_notes:
        payload["free_text_notes"] = free_text_notes
    return payload


def build_time_window_payload(
    *,
    template_id: str,
    mic_id: str,
    time_range_start_unix: int,
    time_range_end_unix: int,
    instructions: str = "",
    reference_data: dict[str, Any] | None = None,
    checks: list[dict[str, Any]] | None = None,
    external_reference_id: str = "",
    free_text_notes: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": template_id,
        "mic_ids": [mic_id],
        "time_range_start_unix": time_range_start_unix,
        "time_range_end_unix": time_range_end_unix,
    }
    if instructions:
        payload["instructions"] = instructions
    if reference_data:
        payload["reference_data"] = reference_data
    if checks:
        payload["checks"] = checks
    if external_reference_id:
        payload["external_reference_id"] = external_reference_id
    if free_text_notes:
        payload["free_text_notes"] = free_text_notes
    return payload


def create_job(payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
    idem_key = idempotency_key or f"sandbox-{uuid.uuid4().hex[:16]}"
    data = request_json(
        "POST",
        "/jobs",
        body=payload,
        extra_headers={"Idempotency-Key": idem_key},
    )
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected create-job response")
    return data


def create_from_fixture(
    fixture_id: str,
    *,
    instructions: str = "",
    reference_data: dict[str, Any] | None = None,
    checks: list[dict[str, Any]] | None = None,
    external_reference_id: str = "",
    free_text_notes: str = "",
    avoid_cache: bool = False,
) -> dict[str, Any]:
    fixture = get_fixture_by_id(fixture_id)
    if avoid_cache:
        run_tag = uuid.uuid4().hex[:8]
        instructions = " ".join(
            part for part in [instructions.strip(), f"[sandbox-run:{run_tag}]"] if part
        ).strip()
    payload = build_fixture_payload(
        fixture,
        instructions=instructions,
        reference_data=reference_data,
        checks=checks,
        external_reference_id=external_reference_id,
        free_text_notes=free_text_notes,
    )
    return create_job(payload, idempotency_key=f"sandbox-{fixture_id}-{uuid.uuid4().hex[:12]}")


def upload_live_audio(
    *,
    file_path: str,
    mic_name: str = "",
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    resolved_path = resolve_local_path(file_path)
    if not resolved_path.exists():
        raise RuntimeError(f"Audio file not found: {resolved_path}")
    if not resolved_path.is_file():
        raise RuntimeError(f"Audio path is not a file: {resolved_path}")

    data = request_multipart(
        "/sandbox/audio",
        file_path=resolved_path,
        form_fields={
            key: value
            for key, value in {
                "mic_name": mic_name.strip(),
                "name": name.strip(),
                "description": description.strip(),
            }.items()
            if value
        },
    )
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected live-audio upload response")
    return data


def create_from_live_audio(
    *,
    file_path: str,
    template_id: str,
    instructions: str = "",
    reference_data: dict[str, Any] | None = None,
    checks: list[dict[str, Any]] | None = None,
    external_reference_id: str = "",
    free_text_notes: str = "",
    mic_name: str = "",
    name: str = "",
    description: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    uploaded = upload_live_audio(
        file_path=file_path,
        mic_name=mic_name,
        name=name,
        description=description,
    )
    payload = build_time_window_payload(
        template_id=template_id,
        mic_id=str(uploaded["mic_id"]),
        time_range_start_unix=int(uploaded["time_range_start_unix"]),
        time_range_end_unix=int(uploaded["time_range_end_unix"]),
        instructions=instructions,
        reference_data=reference_data,
        checks=checks,
        external_reference_id=external_reference_id,
        free_text_notes=free_text_notes,
    )
    job = create_job(payload, idempotency_key=f"live-audio-{uuid.uuid4().hex[:16]}")
    return uploaded, job


def get_job(job_id: str) -> dict[str, Any]:
    data = request_json("GET", f"/jobs/{job_id}")
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected job response")
    return data


def poll_job(job_id: str, interval_seconds: float, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while True:
        job = get_job(job_id)
        status = str(job.get("status", ""))
        print(f"[poll] job_id={job_id} status={status}")
        if status in TERMINAL_STATUSES:
            return job
        if time.time() >= deadline:
            raise RuntimeError(f"Timed out waiting for terminal status for job {job_id}")
        time.sleep(interval_seconds)


def read_json_file(path_text: str) -> Any:
    path = resolve_local_path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


def read_webhook_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def recent_event_summary(events: list[dict[str, Any]], limit: int = 5) -> str:
    if not events:
        return "no receiver events found"
    rows: list[str] = []
    for event in events[-limit:]:
        payload = event.get("payload")
        job_id = ""
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                obj = data.get("object")
                if isinstance(obj, dict):
                    job_id = str(obj.get("job_id", ""))
        event_type = str(event.get("event_type", ""))
        event_id = str(event.get("event_id", ""))
        suffix = f" job_id={job_id}" if job_id else ""
        rows.append(f"{event_type or '<missing>'} event_id={event_id}{suffix}")
    return "; ".join(rows)


def find_job_webhook_event(events: list[dict[str, Any]], *, job_id: str, event_type: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        obj = data.get("object")
        if not isinstance(obj, dict):
            continue
        if obj.get("job_id") == job_id:
            return event
    return None


def wait_for_job_webhook_event(
    *,
    job_id: str,
    event_type: str,
    log_path: Path,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while True:
        events = read_webhook_events(log_path)
        event = find_job_webhook_event(events, job_id=job_id, event_type=event_type)
        if event is not None:
            return event
        if time.time() >= deadline:
            raise RuntimeError(
                "Timed out waiting for webhook "
                f"{event_type} for job {job_id}. "
                f"Checked receiver log: {log_path}. "
                f"Recent events: {recent_event_summary(events)}"
            )
        time.sleep(interval_seconds)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_terminal_job(job: dict[str, Any], scenario: dict[str, Any]) -> None:
    expect(job.get("environment") == "sandbox", f"{scenario['fixture_id']}: expected environment=sandbox")
    expect(job.get("template_id") == scenario["template_id"], f"{scenario['fixture_id']}: unexpected template_id")
    expect(job.get("status") == scenario["expected_status"], f"{scenario['fixture_id']}: unexpected terminal status")
    expect(job.get("result_kind") == scenario["template_id"], f"{scenario['fixture_id']}: unexpected result_kind")
    expect(job.get("schema_version") == "v1", f"{scenario['fixture_id']}: unexpected schema_version")

    external_reference_id = scenario.get("external_reference_id", "")
    if external_reference_id:
        expect(
            job.get("external_reference_id") == external_reference_id,
            f"{scenario['fixture_id']}: external_reference_id was not echoed",
        )

    if scenario["expected_status"] == "completed":
        result = job.get("result")
        expect(isinstance(result, dict), f"{scenario['fixture_id']}: result missing")
        for key in scenario.get("expected_result_keys", []):
            expect(key in result, f"{scenario['fixture_id']}: result missing key {key}")
        audio_artifact = job.get("audio_artifact")
        expect(isinstance(audio_artifact, dict), f"{scenario['fixture_id']}: audio_artifact missing")
        expect(
            str(audio_artifact.get("status", "")) in {"ready", "failed"},
            f"{scenario['fixture_id']}: unexpected audio_artifact status",
        )
    elif scenario["expected_status"] == "refunded":
        expect(job.get("failure_reason") == "sandbox_refunded_fixture", f"{scenario['fixture_id']}: unexpected refund reason")


def smoke_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "fixture_id": "generic_success",
            "template_id": "generic.analysis.v1",
            "expected_status": "completed",
            "expected_event_type": "job.completed",
            "expected_result_keys": ["core_analysis"],
            "instructions": "Return the standard generic analysis contract for smoke validation.",
            "external_reference_id": "smoke-generic-success",
        },
        {
            "fixture_id": "verification_success",
            "template_id": "verification_plus_generic.v1",
            "expected_status": "completed",
            "expected_event_type": "job.completed",
            "expected_result_keys": ["core_analysis", "verification"],
            "instructions": "Verify route, fare, and ticket issuance while also returning the general analysis section.",
            "external_reference_id": "smoke-verification-success",
            "reference_data": {
                "entity_type": "ticket",
                "data": {
                    "ticket_number": "TN-2481",
                    "source": "Aundh",
                    "destination": "Swargate",
                    "fare": "25",
                    "passenger_count": "1",
                },
            },
            "checks": [
                {
                    "id": "route_match",
                    "label": "Route matches ticket",
                    "match_type": "semantic",
                    "expected": {"source": "Aundh", "destination": "Swargate"},
                },
                {
                    "id": "fare_match",
                    "label": "Fare matches ticket",
                    "match_type": "exact",
                    "expected": "25",
                },
                {
                    "id": "ticket_issued",
                    "label": "Ticket was issued",
                    "match_type": "boolean",
                    "expected": True,
                },
            ],
        },
        {
            "fixture_id": "verification_refunded",
            "template_id": "verification.generic.v1",
            "expected_status": "refunded",
            "expected_event_type": "job.refunded",
            "instructions": "Exercise the refunded verification terminal path without retry loops.",
            "external_reference_id": "smoke-verification-refunded",
            "reference_data": {
                "entity_type": "ticket",
                "data": {
                    "ticket_number": "TN-9999",
                    "source": "Sandbox Depot",
                    "destination": "Failure Desk",
                    "fare": "40",
                },
            },
            "checks": [
                {
                    "id": "fare_match",
                    "label": "Fare matches ticket",
                    "match_type": "exact",
                    "expected": "40",
                }
            ],
        },
    ]


def run_smoke_suite(
    *,
    interval_seconds: float,
    timeout_seconds: float,
    verify_webhooks: bool,
    webhook_timeout_seconds: float,
) -> dict[str, Any]:
    templates = list_templates()
    fixtures = {fixture["fixture_id"]: fixture for fixture in list_fixtures()}

    required_templates = {
        "generic.analysis.v1",
        "verification.generic.v1",
        "verification_plus_generic.v1",
    }
    template_ids = {str(template.get("template_id")) for template in templates}
    missing_templates = sorted(required_templates - template_ids)
    expect(not missing_templates, f"missing required templates: {', '.join(missing_templates)}")

    log_path = WEBHOOK_EVENT_LOG
    results: list[dict[str, Any]] = []
    created_job_ids: list[str] = []

    for scenario in smoke_scenarios():
        fixture = fixtures.get(scenario["fixture_id"])
        expect(fixture is not None, f"missing required fixture {scenario['fixture_id']}")
        run_tag = uuid.uuid4().hex[:8]
        instructions = " ".join(
            part for part in [str(scenario.get("instructions", "")).strip(), f"[smoke-run:{run_tag}]"] if part
        ).strip()
        external_reference_id = str(scenario.get("external_reference_id", "")).strip()
        if external_reference_id:
            external_reference_id = f"{external_reference_id}-{run_tag}"

        payload = build_fixture_payload(
            fixture,
            instructions=instructions,
            reference_data=scenario.get("reference_data"),
            checks=scenario.get("checks"),
            external_reference_id=external_reference_id,
        )
        print(f"[smoke] creating {scenario['fixture_id']}")
        created = create_job(payload, idempotency_key=f"smoke-{scenario['fixture_id']}-{uuid.uuid4().hex[:10]}")
        job_id = str(created["job_id"])
        created_job_ids.append(job_id)
        if created.get("cached"):
            print(f"[smoke] fixture={scenario['fixture_id']} reused cached job {job_id}")

        terminal = poll_job(job_id, interval_seconds, timeout_seconds)
        scenario_with_runtime_values = dict(scenario)
        scenario_with_runtime_values["external_reference_id"] = external_reference_id
        validate_terminal_job(terminal, scenario_with_runtime_values)

        webhook_event_id = ""
        if verify_webhooks:
            event = wait_for_job_webhook_event(
                job_id=job_id,
                event_type=str(scenario["expected_event_type"]),
                log_path=log_path,
                timeout_seconds=webhook_timeout_seconds,
            )
            webhook_event_id = str(event.get("event_id", ""))

        results.append(
            {
                "fixture_id": scenario["fixture_id"],
                "job_id": job_id,
                "status": terminal["status"],
                "cached": bool(terminal.get("cached")),
                "result_kind": terminal.get("result_kind"),
                "external_reference_id": terminal.get("external_reference_id", ""),
                "audio_artifact_status": (terminal.get("audio_artifact") or {}).get("status"),
                "webhook_event_id": webhook_event_id,
            }
        )

    recent_jobs = list_jobs(limit=20)
    recent_job_ids = {str(job.get("job_id", "")) for job in recent_jobs}
    for job_id in created_job_ids:
        expect(job_id in recent_job_ids, f"recent job list did not include {job_id}")

    return {
        "api_base": API_BASE,
        "verified_webhooks": verify_webhooks,
        "templates_checked": sorted(required_templates),
        "jobs_created": results,
    }


def cmd_list_templates(_: argparse.Namespace) -> int:
    print(pretty(list_templates()))
    return 0


def cmd_list_fixtures(_: argparse.Namespace) -> int:
    print(pretty(list_fixtures()))
    return 0


def cmd_list_jobs(args: argparse.Namespace) -> int:
    print(pretty(list_jobs(limit=args.limit, status=args.status)))
    return 0


def cmd_get_job(args: argparse.Namespace) -> int:
    print(pretty(get_job(args.job_id)))
    return 0


def cmd_create_from_fixture(args: argparse.Namespace) -> int:
    reference_data = read_json_file(args.reference_data_file) if args.reference_data_file else None
    checks = read_json_file(args.checks_file) if args.checks_file else None
    job = create_from_fixture(
        args.fixture_id,
        instructions=args.instructions,
        reference_data=reference_data,
        checks=checks,
        external_reference_id=args.external_reference_id,
        free_text_notes=args.free_text_notes,
        avoid_cache=args.avoid_cache,
    )
    print(pretty(job))
    if args.wait_webhook:
        event = wait_for_job_webhook_event(
            job_id=str(job["job_id"]),
            event_type=args.expected_event_type,
            log_path=WEBHOOK_EVENT_LOG,
            timeout_seconds=args.webhook_timeout_seconds,
        )
        print(pretty({"webhook_event": event}))
    if args.poll:
        terminal = poll_job(job["job_id"], args.interval_seconds, args.timeout_seconds)
        print(pretty(terminal))
    return 0


def cmd_create_from_live_audio(args: argparse.Namespace) -> int:
    reference_data = read_json_file(args.reference_data_file) if args.reference_data_file else None
    checks = read_json_file(args.checks_file) if args.checks_file else None
    uploaded = upload_live_audio(
        file_path=args.file,
        mic_name=args.mic_name,
        name=args.name,
        description=args.description,
    )
    print(pretty({"uploaded_fixture": uploaded}))
    payload = build_time_window_payload(
        template_id=args.template_id,
        mic_id=str(uploaded["mic_id"]),
        time_range_start_unix=int(uploaded["time_range_start_unix"]),
        time_range_end_unix=int(uploaded["time_range_end_unix"]),
        instructions=args.instructions,
        reference_data=reference_data,
        checks=checks,
        external_reference_id=args.external_reference_id,
        free_text_notes=args.free_text_notes,
    )
    job = create_job(payload, idempotency_key=f"live-audio-{uuid.uuid4().hex[:16]}")
    print(pretty({"job": job}))
    if args.wait_webhook:
        event = wait_for_job_webhook_event(
            job_id=str(job["job_id"]),
            event_type=args.expected_event_type,
            log_path=WEBHOOK_EVENT_LOG,
            timeout_seconds=args.webhook_timeout_seconds,
        )
        print(pretty({"webhook_event": event}))
    if args.poll:
        terminal = poll_job(job["job_id"], args.interval_seconds, args.timeout_seconds)
        print(pretty(terminal))
    return 0


def cmd_poll_job(args: argparse.Namespace) -> int:
    print(pretty(poll_job(args.job_id, args.interval_seconds, args.timeout_seconds)))
    return 0


def cmd_run_smoke_suite(args: argparse.Namespace) -> int:
    report = run_smoke_suite(
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        verify_webhooks=args.verify_webhooks,
        webhook_timeout_seconds=args.webhook_timeout_seconds,
    )
    print(pretty(report))
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    print(pretty(config_snapshot()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KnownSense hosted sandbox test client")
    sub = parser.add_subparsers(dest="command", required=True)

    list_templates_cmd = sub.add_parser("list-templates", help="List public analysis templates")
    list_templates_cmd.set_defaults(func=cmd_list_templates)

    list_fixtures_cmd = sub.add_parser("list-fixtures", help="List hosted sandbox fixtures")
    list_fixtures_cmd.set_defaults(func=cmd_list_fixtures)

    show_config_cmd = sub.add_parser("show-config", help="Show resolved config values and their sources")
    show_config_cmd.set_defaults(func=cmd_show_config)

    list_jobs_cmd = sub.add_parser("list-jobs", help="List analysis jobs for the current workspace")
    list_jobs_cmd.add_argument("--limit", type=int, default=20, help="Maximum jobs to return")
    list_jobs_cmd.add_argument("--status", default="", help="Optional status filter")
    list_jobs_cmd.set_defaults(func=cmd_list_jobs)

    get_job_cmd = sub.add_parser("get-job", help="Fetch one analysis job")
    get_job_cmd.add_argument("--job-id", required=True, help="Analysis job ID")
    get_job_cmd.set_defaults(func=cmd_get_job)

    create_cmd = sub.add_parser("create-from-fixture", help="Create a sandbox job from a seeded fixture")
    create_cmd.add_argument("--fixture-id", required=True, help="Fixture ID from list-fixtures")
    create_cmd.add_argument("--instructions", default="", help="Optional bounded instructions")
    create_cmd.add_argument("--reference-data-file", default="", help="Path to a JSON file for reference_data")
    create_cmd.add_argument("--checks-file", default="", help="Path to a JSON file for checks[]")
    create_cmd.add_argument("--external-reference-id", default="", help="Optional correlation ID echoed by the API")
    create_cmd.add_argument("--free-text-notes", default="", help="Optional free_text_notes field")
    create_cmd.add_argument(
        "--avoid-cache",
        action="store_true",
        help="Append a unique sandbox run tag to instructions so repeated tests create a fresh job",
    )
    create_cmd.add_argument(
        "--wait-webhook",
        action="store_true",
        help="Wait for a matching terminal webhook in the local receiver log instead of relying only on polling",
    )
    create_cmd.add_argument(
        "--expected-event-type",
        default="job.completed",
        help="Terminal event type to wait for when --wait-webhook is enabled",
    )
    create_cmd.add_argument(
        "--webhook-timeout-seconds",
        type=float,
        default=120.0,
        help="Webhook wait timeout when --wait-webhook is enabled",
    )
    create_cmd.add_argument("--poll", action="store_true", help="Poll until terminal status")
    create_cmd.add_argument("--interval-seconds", type=float, default=3.0, help="Polling interval")
    create_cmd.add_argument("--timeout-seconds", type=float, default=90.0, help="Polling timeout")
    create_cmd.set_defaults(func=cmd_create_from_fixture)

    create_live_audio_cmd = sub.add_parser(
        "create-from-live-audio",
        help="Upload sandbox audio and immediately create a live analysis job",
    )
    create_live_audio_cmd.add_argument("--file", required=True, help="Path to an audio file")
    create_live_audio_cmd.add_argument(
        "--template-id",
        default="generic.analysis.v1",
        help="Template ID to use for the analysis job",
    )
    create_live_audio_cmd.add_argument("--mic-name", default="", help="Optional sandbox mic display name")
    create_live_audio_cmd.add_argument("--name", default="", help="Optional live-audio fixture name")
    create_live_audio_cmd.add_argument("--description", default="", help="Optional live-audio fixture description")
    create_live_audio_cmd.add_argument("--instructions", default="", help="Optional bounded instructions")
    create_live_audio_cmd.add_argument("--reference-data-file", default="", help="Path to a JSON file for reference_data")
    create_live_audio_cmd.add_argument("--checks-file", default="", help="Path to a JSON file for checks[]")
    create_live_audio_cmd.add_argument("--external-reference-id", default="", help="Optional correlation ID echoed by the API")
    create_live_audio_cmd.add_argument("--free-text-notes", default="", help="Optional free_text_notes field")
    create_live_audio_cmd.add_argument(
        "--wait-webhook",
        action="store_true",
        help="Wait for a matching terminal webhook in the local receiver log",
    )
    create_live_audio_cmd.add_argument(
        "--expected-event-type",
        default="job.completed",
        help="Terminal event type to wait for when --wait-webhook is enabled",
    )
    create_live_audio_cmd.add_argument(
        "--webhook-timeout-seconds",
        type=float,
        default=120.0,
        help="Webhook wait timeout when --wait-webhook is enabled",
    )
    create_live_audio_cmd.add_argument("--poll", action="store_true", help="Poll until terminal status")
    create_live_audio_cmd.add_argument("--interval-seconds", type=float, default=3.0, help="Polling interval")
    create_live_audio_cmd.add_argument("--timeout-seconds", type=float, default=120.0, help="Polling timeout")
    create_live_audio_cmd.set_defaults(func=cmd_create_from_live_audio)

    poll_cmd = sub.add_parser("poll-job", help="Poll a sandbox job until terminal status")
    poll_cmd.add_argument("--job-id", required=True, help="Analysis job ID")
    poll_cmd.add_argument("--interval-seconds", type=float, default=3.0, help="Polling interval")
    poll_cmd.add_argument("--timeout-seconds", type=float, default=90.0, help="Polling timeout")
    poll_cmd.set_defaults(func=cmd_poll_job)

    smoke_cmd = sub.add_parser("run-smoke-suite", help="Run end-to-end sandbox smoke scenarios")
    smoke_cmd.add_argument("--interval-seconds", type=float, default=2.0, help="Job polling interval")
    smoke_cmd.add_argument("--timeout-seconds", type=float, default=90.0, help="Job polling timeout")
    smoke_cmd.add_argument("--verify-webhooks", action="store_true", help="Wait for terminal webhook events in the local receiver log")
    smoke_cmd.add_argument("--webhook-timeout-seconds", type=float, default=90.0, help="Webhook wait timeout")
    smoke_cmd.set_defaults(func=cmd_run_smoke_suite)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from tests.api.conftest import XLSX_CT
from tests.helpers.jobbus import MemoryJobBus
from tests.helpers.xlsx import write_zip

from cashflow_audit.app.ids import audit_id_for, sha256_bytes
from cashflow_audit.ports.protocols import JobState
from cashflow_audit.store.fs import write_json


@contextmanager
def api_client(tmp_path: Path, **kwargs: Any) -> Iterator[tuple[TestClient, Path, MemoryJobBus]]:
    from cashflow_audit.api.app import create_app

    bus = kwargs.pop("bus", None)
    if bus is None:
        bus = MemoryJobBus()
    assert isinstance(bus, MemoryJobBus)
    data_root = tmp_path / "data"
    app = create_app(data_root=data_root, bus=bus, **kwargs)
    with TestClient(app) as client:
        yield client, data_root, bus


def _post(
    client: TestClient,
    book: Path,
    *,
    actor: str = "u1",
    filename: str = "m.xlsx",
    content_type: str = XLSX_CT,
    data: bytes | None = None,
):
    payload = book.read_bytes() if data is None else data
    headers = {}
    if actor:
        headers["X-Actor-Id"] = actor
    return client.post(
        "/v1/audits",
        headers=headers,
        files={"file": (filename, payload, content_type)},
    )


def _seed_report(
    dest: Path, *, actor: str, filename: str, sha: str, questions: list | None = None
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    write_json(
        dest / "owner.json",
        {"actor_id": actor, "content_sha256": sha, "source_filename": filename},
    )
    qs = questions if questions is not None else []
    write_json(
        dest / "report.json",
        {
            "audit_id": dest.name,
            "source_filename": filename,
            "sha256": sha,
            "status": "needs_input" if qs else "succeeded",
            "llm_used": False,
            "embeddings_used": False,
            "summary": {
                "findings": 0,
                "by_severity": {"error": 0, "warning": 0, "risk": 0},
                "questions": len(qs),
            },
            "findings": [],
            "questions": qs,
        },
    )
    meta_status = "needs_input" if qs else "succeeded"
    write_json(dest / "meta.json", {"status": meta_status, "stage": "done"})


def test_healthz(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        res = client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


def test_readyz_ready_when_redis_up(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        res = client.get("/readyz")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ready"
        assert body["redis"] is True


def test_readyz_degraded_when_llm_down(tmp_path: Path) -> None:
    with api_client(tmp_path, llm_ok=False) as (client, _data, _bus):
        res = client.get("/readyz")
        assert res.status_code == 200
        assert res.json()["status"] == "degraded"
        assert res.json()["llm"] is False


def test_readyz_not_ready_when_redis_down(tmp_path: Path) -> None:
    with api_client(tmp_path, bus=MemoryJobBus(redis_up=False)) as (client, _data, _bus):
        res = client.get("/readyz")
        assert res.status_code == 503
        assert res.json()["status"] == "not_ready"
        assert res.json()["redis"] is False


def test_post_missing_actor(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        res = _post(client, book, actor="")
        assert res.status_code == 400
        assert res.json()["error"] == "missing_actor"


def test_post_unsupported_extension(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        res = _post(client, book, filename="m.xls")
        assert res.status_code == 400
        assert res.json()["error"] == "unsupported_media_type"


def test_post_empty_file(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        res = _post(client, book, data=b"")
        assert res.status_code == 400
        assert res.json()["error"] == "empty_file"


def test_post_file_too_large(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path, max_upload_bytes=16) as (client, _data, _bus):
        res = _post(client, book, data=b"x" * 32)
        assert res.status_code == 413
        assert res.json()["error"] == "file_too_large"


def test_post_encrypted_workbook(tmp_path: Path) -> None:
    source = tmp_path / "enc.xlsx"
    write_zip(source, {"EncryptionInfo": b"ole", "EncryptedPackage": b"cipher"})
    with api_client(tmp_path) as (client, _data, _bus):
        res = _post(client, source)
        assert res.status_code == 422
        assert res.json()["error"] == "encrypted_workbook"


def test_post_new_file_queued(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, data_root, bus):
        res = _post(client, book)
        assert res.status_code == 202
        body = res.json()
        assert body["status"] == "queued"
        assert body["stage"] == "queued"
        audit_id = body["audit_id"]
        expected = audit_id_for("u1", sha256_bytes(book.read_bytes()))
        assert audit_id == expected
        dest = data_root / "audits" / audit_id
        assert (dest / "source.xlsx").is_file()
        owner = json.loads((dest / "owner.json").read_text())
        assert owner["actor_id"] == "u1"
        assert bus.enqueue_calls == [audit_id]


def test_post_same_file_while_queued_does_not_reenqueue(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, bus):
        first = _post(client, book)
        second = _post(client, book)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["audit_id"] == second.json()["audit_id"]
        assert bus.enqueue_calls == [first.json()["audit_id"]]


def test_post_existing_report_returns_200(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    with api_client(tmp_path) as (client, data_root, bus):
        _seed_report(data_root / "audits" / audit_id, actor="u1", filename="m.xlsx", sha=sha)
        res = _post(client, book)
        assert res.status_code == 200
        body = res.json()
        assert body["audit_id"] == audit_id
        assert body["status"] == "succeeded"
        assert body["report_url"] == f"/v1/audits/{audit_id}/report"
        assert bus.enqueue_calls == []
        bus.live[audit_id] = JobState(status="succeeded", stage="done", actor_id="u1")
        again = _post(client, book)
        assert again.status_code == 200
        assert bus.live[audit_id].status == "succeeded"
        assert bus.enqueue_calls == []


def test_different_actors_get_different_ids(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        a = _post(client, book, actor="alice")
        b = _post(client, book, actor="bob")
        assert a.json()["audit_id"] != b.json()["audit_id"]


def test_get_unknown_404(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        res = client.get("/v1/audits/" + "a" * 64, headers={"X-Actor-Id": "u1"})
        assert res.status_code == 404
        assert res.json()["error"] == "not_found"


def test_get_wrong_actor_403(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        posted = _post(client, book, actor="alice")
        audit_id = posted.json()["audit_id"]
        res = client.get(f"/v1/audits/{audit_id}", headers={"X-Actor-Id": "bob"})
        assert res.status_code == 403
        assert res.json()["error"] == "forbidden"


def test_get_queued_and_retry_after(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        posted = _post(client, book)
        audit_id = posted.json()["audit_id"]
        res = client.get(f"/v1/audits/{audit_id}", headers={"X-Actor-Id": "u1"})
        assert res.status_code == 200
        assert res.json()["status"] == "queued"
        assert res.headers.get("Retry-After") == "2"


def test_get_queued_after_bus_lost(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, bus):
        posted = _post(client, book)
        audit_id = posted.json()["audit_id"]
        bus.live.clear()
        res = client.get(f"/v1/audits/{audit_id}", headers={"X-Actor-Id": "u1"})
        assert res.status_code == 200
        assert res.json()["status"] == "queued"


def test_get_failed_from_meta(tmp_path: Path) -> None:
    with api_client(tmp_path) as (client, data_root, _bus):
        dest = data_root / "audits" / ("b" * 64)
        dest.mkdir(parents=True)
        write_json(
            dest / "owner.json",
            {"actor_id": "u1", "content_sha256": "abc", "source_filename": "broken.xlsx"},
        )
        write_json(
            dest / "meta.json",
            {"status": "failed", "stage": "parse", "error": "zip_rejected"},
        )
        res = client.get(f"/v1/audits/{dest.name}", headers={"X-Actor-Id": "u1"})
        body = res.json()
        assert res.status_code == 200
        assert body["status"] == "failed"
        assert body["error"] == "zip_rejected"
        assert "report_url" not in body


def test_get_report_not_ready(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        posted = _post(client, book)
        audit_id = posted.json()["audit_id"]
        res = client.get(f"/v1/audits/{audit_id}/report", headers={"X-Actor-Id": "u1"})
        assert res.status_code == 409
        assert res.json()["error"] == "report_not_ready"
        assert res.json()["status"] == "queued"


def test_get_report_200(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    with api_client(tmp_path) as (client, data_root, _bus):
        _seed_report(data_root / "audits" / audit_id, actor="u1", filename="m.xlsx", sha=sha)
        res = client.get(f"/v1/audits/{audit_id}/report", headers={"X-Actor-Id": "u1"})
        assert res.status_code == 200
        assert res.json()["audit_id"] == audit_id
        assert res.json()["sha256"] == sha


def test_answers_requeues(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    questions = [
        {
            "id": "q_001",
            "kind": "mapping",
            "prompt": "Строка «Revenue» — это pnl.revenue?",
            "cell_refs": ["P&L!A2"],
            "options": ["pnl.revenue", "unknown"],
        }
    ]
    with api_client(tmp_path) as (client, data_root, bus):
        dest = data_root / "audits" / audit_id
        _seed_report(dest, actor="u1", filename="m.xlsx", sha=sha, questions=questions)
        bus.live[audit_id] = JobState(status="needs_input", stage="done", actor_id="u1")
        write_json(
            dest / "mapping.json",
            {
                "rows": [
                    {
                        "row_key": "P&L|2|P&L!r1",
                        "sheet": "P&L",
                        "row": 2,
                        "block_id": "P&L!r1",
                        "label": "Revenue",
                        "parent_label": None,
                        "concept_id": None,
                        "article_role": "calculation",
                        "source": "question",
                    }
                ],
                "questions": questions,
            },
        )
        res = client.post(
            f"/v1/audits/{audit_id}/answers",
            headers={"X-Actor-Id": "u1"},
            json={"answers": [{"question_id": "q_001", "concept_id": "pnl.revenue"}]},
        )
        assert res.status_code == 202
        assert res.json()["status"] == "queued"
        assert not (dest / "report.json").exists()
        assert not (dest / "mapping.json").exists()
        assert (dest / "owner.json").is_file()
        assert bus.enqueue_calls == [audit_id]
        assert bus.live[audit_id].status == "queued"


def test_answers_unknown_question_422(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    questions = [
        {
            "id": "q_001",
            "kind": "mapping",
            "prompt": "x",
            "cell_refs": ["P&L!A2"],
            "options": ["pnl.revenue", "unknown"],
        }
    ]
    with api_client(tmp_path) as (client, data_root, _bus):
        _seed_report(
            data_root / "audits" / audit_id,
            actor="u1",
            filename="m.xlsx",
            sha=sha,
            questions=questions,
        )
        res = client.post(
            f"/v1/audits/{audit_id}/answers",
            headers={"X-Actor-Id": "u1"},
            json={"answers": [{"question_id": "nope", "concept_id": "pnl.revenue"}]},
        )
        assert res.status_code == 422


def test_answers_running_409(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    questions = [
        {
            "id": "q_001",
            "kind": "mapping",
            "prompt": "x",
            "cell_refs": ["P&L!A2"],
            "options": ["pnl.revenue"],
        }
    ]
    with api_client(tmp_path) as (client, data_root, bus):
        _seed_report(
            data_root / "audits" / audit_id,
            actor="u1",
            filename="m.xlsx",
            sha=sha,
            questions=questions,
        )
        bus.live[audit_id] = JobState(status="running", stage="check", actor_id="u1")
        res = client.post(
            f"/v1/audits/{audit_id}/answers",
            headers={"X-Actor-Id": "u1"},
            json={"answers": [{"question_id": "q_001", "concept_id": "pnl.revenue"}]},
        )
        assert res.status_code == 409


def test_worker_completes_report(tmp_path: Path, book: Path) -> None:
    import time

    with api_client(tmp_path, run_workers=True, worker_concurrency=1) as (client, _data, _bus):
        posted = _post(client, book)
        audit_id = posted.json()["audit_id"]
        status = "queued"
        for _ in range(120):
            res = client.get(f"/v1/audits/{audit_id}", headers={"X-Actor-Id": "u1"})
            status = res.json()["status"]
            if status not in {"queued", "running"}:
                break
            time.sleep(0.5)
        assert status in {"succeeded", "needs_input", "degraded"}
        report = client.get(f"/v1/audits/{audit_id}/report", headers={"X-Actor-Id": "u1"})
        assert report.status_code == 200
        assert report.json()["audit_id"] == audit_id


def test_answers_no_questions_409(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    with api_client(tmp_path) as (client, data_root, _bus):
        _seed_report(data_root / "audits" / audit_id, actor="u1", filename="m.xlsx", sha=sha)
        res = client.post(
            f"/v1/audits/{audit_id}/answers",
            headers={"X-Actor-Id": "u1"},
            json={"answers": [{"question_id": "q_001", "concept_id": "pnl.revenue"}]},
        )
        assert res.status_code == 409


def test_healthz_and_readyz_log_http_request(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    with api_client(tmp_path) as (client, _data, _bus):
        caplog.clear()
        res = client.get("/healthz")
        assert res.status_code == 200
        ready = client.get("/readyz")
        assert ready.status_code == 200
    events = [
        (r.__dict__.get("path"), r.__dict__.get("method"), r.__dict__.get("http_code"))
        for r in caplog.records
        if r.__dict__.get("event") == "http_request"
    ]
    assert ("/healthz", "GET", 200) in events
    assert ("/readyz", "GET", 200) in events


def test_post_does_not_log_upload_filename(tmp_path: Path, book: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    with api_client(tmp_path) as (client, _data, _bus):
        caplog.clear()
        res = _post(client, book, filename="ClientCashflow.xlsx")
        assert res.status_code == 202
    assert "ClientCashflow" not in caplog.text
    assert any(
        r.__dict__.get("event") == "http_request"
        and r.__dict__.get("path") == "/v1/audits"
        and r.__dict__.get("method") == "POST"
        and r.__dict__.get("http_code") == 202
        for r in caplog.records
    )


def _http_requests(caplog):
    return [r for r in caplog.records if r.__dict__.get("event") == "http_request"]


def test_audit_routes_log_http_request_with_audit_id(
    tmp_path: Path, book: Path, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    with api_client(tmp_path) as (client, _data, _bus):
        caplog.clear()
        created = _post(client, book)
        assert created.status_code == 202
        audit_id = created.json()["audit_id"]
        post = _http_requests(caplog)
        assert post
        assert post[-1].__dict__.get("path") == "/v1/audits"
        assert post[-1].__dict__.get("audit_id") == audit_id
        assert post[-1].__dict__.get("http_code") == 202

        caplog.clear()
        status = client.get(f"/v1/audits/{audit_id}", headers={"X-Actor-Id": "u1"})
        assert status.status_code == 200
        got = _http_requests(caplog)
        assert got
        assert got[-1].__dict__.get("path") == f"/v1/audits/{audit_id}"
        assert got[-1].__dict__.get("audit_id") == audit_id
        assert got[-1].__dict__.get("method") == "GET"

        caplog.clear()
        report = client.get(
            f"/v1/audits/{audit_id}/report", headers={"X-Actor-Id": "u1"}
        )
        assert report.status_code == 409
        rep = _http_requests(caplog)
        assert rep
        assert rep[-1].__dict__.get("path") == f"/v1/audits/{audit_id}/report"
        assert rep[-1].__dict__.get("audit_id") == audit_id
        assert rep[-1].__dict__.get("http_code") == 409

        caplog.clear()
        answers = client.post(
            f"/v1/audits/{audit_id}/answers",
            headers={"X-Actor-Id": "u1"},
            json={"answers": []},
        )
        assert answers.status_code == 409
        ans = _http_requests(caplog)
        assert ans
        assert ans[-1].__dict__.get("path") == f"/v1/audits/{audit_id}/answers"
        assert ans[-1].__dict__.get("audit_id") == audit_id


def test_content_routes_require_actor(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        posted = _post(client, book)
        audit_id = posted.json()["audit_id"]
        for suffix in ("layout", "mapping", "integrity"):
            res = client.get(f"/v1/audits/{audit_id}/{suffix}")
            assert res.status_code == 400
            assert res.json()["error"] == "missing_actor"


def test_content_routes_forbidden_and_not_ready(tmp_path: Path, book: Path) -> None:
    with api_client(tmp_path) as (client, _data, _bus):
        posted = _post(client, book, actor="alice")
        audit_id = posted.json()["audit_id"]
        for suffix, code in (
            ("layout", "layout_not_ready"),
            ("mapping", "mapping_not_ready"),
            ("integrity", "integrity_not_ready"),
        ):
            forbidden = client.get(
                f"/v1/audits/{audit_id}/{suffix}", headers={"X-Actor-Id": "bob"}
            )
            assert forbidden.status_code == 403
            missing = client.get(
                f"/v1/audits/{audit_id}/{suffix}", headers={"X-Actor-Id": "alice"}
            )
            assert missing.status_code == 409
            assert missing.json()["error"] == code


def test_content_routes_200_and_audit_lists_urls(tmp_path: Path, book: Path) -> None:
    data = book.read_bytes()
    sha = sha256_bytes(data)
    audit_id = audit_id_for("u1", sha)
    with api_client(tmp_path) as (client, data_root, _bus):
        dest = data_root / "audits" / audit_id
        _seed_report(dest, actor="u1", filename="m.xlsx", sha=sha)
        write_json(dest / "layout.json", {"sheets": []})
        write_json(dest / "mapping.json", {"rows": [], "questions": []})
        write_json(dest / "integrity.json", {"findings": []})
        layout = client.get(f"/v1/audits/{audit_id}/layout", headers={"X-Actor-Id": "u1"})
        mapping = client.get(
            f"/v1/audits/{audit_id}/mapping", headers={"X-Actor-Id": "u1"}
        )
        integrity = client.get(
            f"/v1/audits/{audit_id}/integrity", headers={"X-Actor-Id": "u1"}
        )
        assert layout.status_code == 200
        assert layout.json() == {"sheets": []}
        assert mapping.status_code == 200
        assert integrity.status_code == 200
        status = client.get(f"/v1/audits/{audit_id}", headers={"X-Actor-Id": "u1"})
        urls = status.json()["content"]
        assert urls["layout"] == f"/v1/audits/{audit_id}/layout"
        assert urls["mapping"] == f"/v1/audits/{audit_id}/mapping"
        assert urls["integrity"] == f"/v1/audits/{audit_id}/integrity"
        assert urls["report"] == f"/v1/audits/{audit_id}/report"

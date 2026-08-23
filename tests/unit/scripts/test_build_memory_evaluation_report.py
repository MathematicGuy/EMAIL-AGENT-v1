from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script, run_cli


def _probe_payload(probe_set_id: str, *, seed_turn: str) -> dict[str, object]:
    return {
        "schema_version": "2.0.0",
        "probe_set_id": probe_set_id,
        "label": probe_set_id,
        "seed": {
            "short_term": [seed_turn],
            "long_term": {},
            "episodic": [],
            "semantic": None,
        },
        "probes": [
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "what did I say?",
                "expect_any": [seed_turn],
            }
        ],
    }


def _write_probe(path: Path, probe_set_id: str, *, seed_turn: str) -> Path:
    path.write_text(
        json.dumps(_probe_payload(probe_set_id, seed_turn=seed_turn)),
        encoding="utf-8",
    )
    return path


def _baseline(
    probe_set_id: str,
    *,
    sha256: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "2.1.0",
        "probe_set_id": probe_set_id,
        "probe_count": 1,
        "provider": "dry-run",
        "model": "scripted",
        "ran_at": "2026-08-21T00:00:00+00:00",
        "run_key": "rk",
        "nonce": "n",
        "per_scope": {},
        "verdicts": [
            {
                "probe": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "full": "pass",
                "ablated": "miss",
                "control": "miss",
                "verdict": "scope_earned_it",
            }
        ],
        "seed_failures": [],
    }
    if sha256 is not None:
        payload["probe_set_sha256"] = sha256
    return payload


def _run_report(
    tmp_path: Path,
    monkeypatch: object,
    *,
    baseline: dict[str, object],
    probes_dir: Path,
    probe_set: Path | None = None,
) -> tuple[int, str, str, Path]:
    module = load_script("build_memory_evaluation_report")
    monkeypatch.setattr(module, "_DEFAULT_PROBES_DIR", probes_dir)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    output = tmp_path / "report.md"
    argv = ["--baseline", str(baseline_path), "--output", str(output)]
    if probe_set is not None:
        argv.extend(["--probe-set", str(probe_set)])
    result = run_cli("build_memory_evaluation_report", *argv)
    return result.returncode, result.stdout, result.stderr, output


def test_report_binds_v2_id_when_v3_file_also_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = tmp_path / "probes"
    probes.mkdir()
    _write_probe(probes / "v2-four-scopes-wide.json", "v2_four_scopes_wide", seed_turn="v2-seed")
    _write_probe(probes / "v3-50-probes.json", "v3_50_probes", seed_turn="v3-seed")

    code, _stdout, stderr, output = _run_report(
        tmp_path,
        monkeypatch,
        baseline=_baseline("v2_four_scopes_wide"),
        probes_dir=probes,
    )
    assert code == 0, stderr
    markdown = output.read_text(encoding="utf-8")
    assert "v2-seed" in markdown
    assert "v3-seed" not in markdown


def test_report_matching_hash_loads_bound_probe_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = tmp_path / "probes"
    probes.mkdir()
    v2 = _write_probe(
        probes / "v2-four-scopes-wide.json", "v2_four_scopes_wide", seed_turn="v2-seed"
    )
    _write_probe(probes / "v3-50-probes.json", "v3_50_probes", seed_turn="v3-seed")
    digest = hashlib.sha256(v2.read_bytes()).hexdigest()

    code, _stdout, stderr, output = _run_report(
        tmp_path,
        monkeypatch,
        baseline=_baseline("v2_four_scopes_wide", sha256=digest),
        probes_dir=probes,
    )
    assert code == 0, stderr
    markdown = output.read_text(encoding="utf-8")
    assert "v2-seed" in markdown
    assert "v3-seed" not in markdown


def test_report_hash_mismatch_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = tmp_path / "probes"
    probes.mkdir()
    v2 = _write_probe(
        probes / "v2-four-scopes-wide.json", "v2_four_scopes_wide", seed_turn="v2-seed"
    )
    _write_probe(probes / "v3-50-probes.json", "v3_50_probes", seed_turn="v3-seed")

    code, _stdout, stderr, output = _run_report(
        tmp_path,
        monkeypatch,
        baseline=_baseline("v2_four_scopes_wide", sha256="0" * 64),
        probes_dir=probes,
    )
    assert code == 1
    assert "ERROR" in stderr
    assert "sha256" in stderr.lower()
    assert not output.exists()
    assert v2.exists()


def test_report_unknown_probe_set_id_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = tmp_path / "probes"
    probes.mkdir()
    _write_probe(probes / "v2-four-scopes-wide.json", "v2_four_scopes_wide", seed_turn="v2-seed")

    code, _stdout, stderr, output = _run_report(
        tmp_path,
        monkeypatch,
        baseline=_baseline("v3_50_probes"),
        probes_dir=probes,
    )
    assert code == 1
    assert "ERROR" in stderr
    assert "v3_50_probes" in stderr
    assert not output.exists()


def _diagnose(**overrides: object) -> tuple[str, str, str]:
    module = load_script("build_memory_evaluation_report")
    kwargs: dict[str, object] = {
        "probe_id": "sem_restraint_02",
        "target_scope": "semantic",
        "test_type": "restraint",
        "verdict": "dangerous",
        "full_outcome": "invented",
        "ablated_outcome": "pass",
        "control_outcome": "pass",
        "full_reply": "",
        "ablated_reply": "Tôi không có thông tin.",
        "control_reply": "Tôi không có thông tin.",
        "seed_failures": (),
    }
    kwargs.update(overrides)
    return module.diagnose_needs_reading_probe(**kwargs)


def test_wrap_invention_with_refusal_phrase_is_concern_d_not_grader() -> None:
    """Refuse-then-recite on full, clean refuse on blind arms, is prompt wrap-invention.

    A refusal substring in the full reply is not Concern A: scoring already
    grades invented_any (and unmatched answering) as invented, and the 3-arm
    pattern (invented, pass, pass) is prompt_fault. Adding grader patterns
    would swallow the near-miss.
    """
    badge, summary, _detail = _diagnose(
        full_reply=(
            "Hiện tại, tài liệu được cung cấp chỉ đề cập đến chính sách công tác "
            "phí trong nước, với mức 450.000 đồng mỗi ngày. Không tìm thấy thông "
            "tin về chính sách công tác phí cho chuyến đi nước ngoài."
        ),
    )
    assert "Concern D" in badge
    assert "Concern A" not in badge
    assert "wrap" in summary.lower() or "bịa" in summary.lower()


def test_unmatched_refusal_on_all_arms_is_still_concern_a() -> None:
    """Blind arms also invented: grader likely missed a refusal phrasing."""
    badge, _summary, _detail = _diagnose(
        full_outcome="invented",
        ablated_outcome="invented",
        control_outcome="invented",
        full_reply="Hiện tại tôi không có thông tin về biểu mẫu đổi laptop hỏng.",
    )
    assert "Concern A" in badge
    assert "Concern D" not in badge


def test_restraint_invention_without_refusal_phrase_is_concern_d() -> None:
    badge, summary, _detail = _diagnose(
        full_reply="Công tác phí nước ngoài là 2.000.000 đồng mỗi ngày.",
    )
    assert "Concern D" in badge
    assert "Concern A" not in badge
    assert "wrap" in summary.lower()

"""Tests voor `polder.llm.session.SkillSession`.

Mockt `subprocess.Popen` zodat er geen `claude`-binary wordt aangeroepen.
"""

from __future__ import annotations

import io
import json
from typing import Any, ClassVar

import pytest

from polder.llm import session as session_mod
from polder.llm.session import (
    CACHE_HIT_THRESHOLD,
    DEFAULT_MODEL,
    MODEL_OVERRIDES,
    SkillResult,
    SkillSession,
)

# ---------------------------------------------------------------------------
# Fake Popen
# ---------------------------------------------------------------------------


class _StdoutPipe:
    """File-like dat lines retourneert via __iter__."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._pos = 0

    def __iter__(self) -> _StdoutPipe:
        return self

    def __next__(self) -> str:
        if self._pos >= len(self._lines):
            raise StopIteration
        line = self._lines[self._pos]
        self._pos += 1
        return line

    def fileno(self) -> int:
        return -1


class _StdinPipe(io.StringIO):
    """StringIO met `closed` property en BrokenPipeError-trigger optie."""

    def __init__(self) -> None:
        super().__init__()
        self._broken = False

    def set_broken(self) -> None:
        self._broken = True

    def write(self, s: str) -> int:  # type: ignore[override]
        if self._broken:
            raise BrokenPipeError("test pipe broken")
        return super().write(s)


class FakePopen:
    """Minimal subprocess.Popen-stand-in voor tests."""

    instances: ClassVar[list[FakePopen]] = []

    def __init__(
        self,
        cmd: list[str],
        *,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
        text: bool = True,
        cwd: Any = None,
        bufsize: int = 0,
    ) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.stdin = _StdinPipe()
        # stdout-lines worden ingesteld via class-attribuut `next_stdout_lines`
        self.stdout = _StdoutPipe(list(self._next_stdout_lines))
        self.stderr = io.StringIO()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        FakePopen.instances.append(self)

    _next_stdout_lines: ClassVar[list[str]] = []

    @classmethod
    def set_stdout(cls, lines: list[str]) -> None:
        cls._next_stdout_lines = lines

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


@pytest.fixture(autouse=True)
def _reset_fake_popen() -> None:
    FakePopen.instances.clear()
    FakePopen._next_stdout_lines = []


@pytest.fixture
def patch_popen(monkeypatch: pytest.MonkeyPatch) -> type[FakePopen]:
    monkeypatch.setattr(session_mod.subprocess, "Popen", FakePopen)
    return FakePopen


# ---------------------------------------------------------------------------
# Constructor / model resolution
# ---------------------------------------------------------------------------


def test_model_overrides_parse_organogram() -> None:
    assert MODEL_OVERRIDES["parse-organogram"] == "claude-opus-4-7"


def test_default_model_constant() -> None:
    assert DEFAULT_MODEL == "claude-haiku-4-5"


def test_constructor_uses_default_for_skill_without_override(
    patch_popen: type[FakePopen],
) -> None:
    # review-pr-diff bestaat als skill maar staat niet in MODEL_OVERRIDES,
    # dus moet hij op DEFAULT_MODEL (Haiku 4.5) draaien.
    s = SkillSession("review-pr-diff")
    assert s.model == DEFAULT_MODEL


def test_constructor_uses_override_for_parse_organogram(patch_popen: type[FakePopen]) -> None:
    s = SkillSession("parse-organogram")
    assert s.model == "claude-opus-4-7"


def test_constructor_explicit_model_wins(patch_popen: type[FakePopen]) -> None:
    s = SkillSession("parse-organogram", model="custom-model")
    assert s.model == "custom-model"


# ---------------------------------------------------------------------------
# _build_cmd flags
# ---------------------------------------------------------------------------


def test_build_cmd_contains_expected_flags(patch_popen: type[FakePopen]) -> None:
    s = SkillSession("parse-abd-nieuws", max_budget_usd=0.42, fallback_model="claude-opus-4-7")
    cmd = s._build_cmd()

    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--input-format" in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--system-prompt-file" in cmd
    skill_path = cmd[cmd.index("--system-prompt-file") + 1]
    assert skill_path.endswith("parse-abd-nieuws/SKILL.md")
    assert "--model" in cmd
    # parse-abd-nieuws zit in MODEL_OVERRIDES → Sonnet, niet de default Haiku
    assert cmd[cmd.index("--model") + 1] == MODEL_OVERRIDES["parse-abd-nieuws"]
    assert "--no-session-persistence" in cmd
    assert "--max-budget-usd" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.42"
    assert "--fallback-model" in cmd
    assert cmd[cmd.index("--fallback-model") + 1] == "claude-opus-4-7"
    assert "--disable-slash-commands" in cmd


def test_build_cmd_no_fallback_when_none(patch_popen: type[FakePopen]) -> None:
    s = SkillSession("parse-abd-nieuws", fallback_model=None)
    cmd = s._build_cmd()
    assert "--fallback-model" not in cmd


def test_build_cmd_appends_extra_args(patch_popen: type[FakePopen]) -> None:
    s = SkillSession("parse-abd-nieuws", extra_args=["--debug", "--foo"])
    cmd = s._build_cmd()
    assert cmd[-2:] == ["--debug", "--foo"]


# ---------------------------------------------------------------------------
# Skill path resolution
# ---------------------------------------------------------------------------


def test_skill_path_raises_for_missing_skill() -> None:
    with pytest.raises(FileNotFoundError):
        SkillSession("does-not-exist-skill")


# ---------------------------------------------------------------------------
# Context manager: enter/exit
# ---------------------------------------------------------------------------


def _result_line(**fields: Any) -> str:
    event = {
        "type": "result",
        "result": fields.get("result", "ok"),
        "total_cost_usd": fields.get("total_cost_usd", 0.0),
        "usage": fields.get(
            "usage",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        ),
    }
    if "is_error" in fields:
        event["is_error"] = fields["is_error"]
    if "api_error_status" in fields:
        event["api_error_status"] = fields["api_error_status"]
    return json.dumps(event) + "\n"


def test_context_manager_starts_and_closes_popen(patch_popen: type[FakePopen]) -> None:
    patch_popen.set_stdout([])
    with SkillSession("parse-abd-nieuws") as s:
        assert s._proc is not None
        assert len(FakePopen.instances) == 1
    # na exit moet _proc weer None zijn
    assert s._proc is None
    # En wait moet zijn aangeroepen (returncode != None)
    assert FakePopen.instances[0].returncode == 0


def test_close_is_idempotent(patch_popen: type[FakePopen]) -> None:
    patch_popen.set_stdout([])
    s = SkillSession("parse-abd-nieuws")
    with s:
        pass
    # tweede close mag niet exploderen
    s.close()


# ---------------------------------------------------------------------------
# call(): stdin payload format
# ---------------------------------------------------------------------------


def test_call_writes_correct_json_to_stdin(patch_popen: type[FakePopen]) -> None:
    patch_popen.set_stdout([_result_line(result="hoi")])
    with SkillSession("parse-abd-nieuws") as s:
        s.call("Hello world")
        stdin_content = FakePopen.instances[0].stdin.getvalue()

    assert stdin_content.endswith("\n")
    parsed = json.loads(stdin_content.strip())
    assert parsed == {
        "type": "user",
        "message": {"role": "user", "content": "Hello world"},
    }


def test_call_with_path_reads_file(patch_popen: type[FakePopen], tmp_path: Any) -> None:
    payload_file = tmp_path / "input.txt"
    payload_file.write_text("file-content-xyz", encoding="utf-8")

    patch_popen.set_stdout([_result_line(result="ok")])
    with SkillSession("parse-abd-nieuws") as s:
        s.call(payload_file)
        stdin_content = FakePopen.instances[0].stdin.getvalue()

    parsed = json.loads(stdin_content.strip())
    assert parsed["message"]["content"] == "file-content-xyz"


# ---------------------------------------------------------------------------
# call(): stdout parsing
# ---------------------------------------------------------------------------


def test_call_skips_system_and_assistant_events(patch_popen: type[FakePopen]) -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init"}) + "\n",
        json.dumps({"type": "assistant", "message": {"content": []}}) + "\n",
        _result_line(result="final"),
    ]
    patch_popen.set_stdout(lines)
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("input")

    assert result.text == "final"
    assert result.is_error is False


def test_call_ignores_invalid_json_lines(patch_popen: type[FakePopen]) -> None:
    lines = [
        "not json at all\n",
        "\n",  # blank line
        _result_line(result="ok"),
    ]
    patch_popen.set_stdout(lines)
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("input")
    assert result.text == "ok"


# ---------------------------------------------------------------------------
# _parse_result: usage / cost / rate-limit / cache-hit
# ---------------------------------------------------------------------------


def test_parse_result_extracts_usage_and_cost(patch_popen: type[FakePopen]) -> None:
    line = _result_line(
        result="hoi",
        total_cost_usd=0.0123,
        usage={
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 7000,
            "cache_creation_input_tokens": 50,
        },
    )
    patch_popen.set_stdout([line])
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("x")

    assert result.cost_usd == pytest.approx(0.0123)
    assert result.input_tokens == 100
    assert result.output_tokens == 200
    assert result.cache_read_tokens == 7000
    assert result.cache_creation_tokens == 50
    assert result.cache_hit is True  # 7000 >= 5000


def test_parse_result_cache_hit_threshold_boundary(patch_popen: type[FakePopen]) -> None:
    line = _result_line(
        usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": CACHE_HIT_THRESHOLD - 1,
            "cache_creation_input_tokens": 0,
        }
    )
    patch_popen.set_stdout([line])
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("x")
    assert result.cache_hit is False


def test_parse_result_api_429_sets_rate_limited(patch_popen: type[FakePopen]) -> None:
    line = _result_line(
        result="error",
        is_error=True,
        api_error_status=429,
    )
    patch_popen.set_stdout([line])
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("x")

    assert result.rate_limited is True
    assert result.is_error is True


def test_parse_result_text_contains_rate_limit_sets_flag(patch_popen: type[FakePopen]) -> None:
    line = _result_line(result="you hit your rate limit, try again")
    patch_popen.set_stdout([line])
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("x")
    assert result.rate_limited is True


def test_parse_result_normal_response_no_rate_limit(patch_popen: type[FakePopen]) -> None:
    line = _result_line(result="just a normal response")
    patch_popen.set_stdout([line])
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("x")
    assert result.rate_limited is False
    assert result.is_error is False


# ---------------------------------------------------------------------------
# Two consecutive calls in same session
# ---------------------------------------------------------------------------


def test_two_calls_same_session(patch_popen: type[FakePopen]) -> None:
    lines = [
        _result_line(result="first", total_cost_usd=0.01),
        _result_line(result="second", total_cost_usd=0.02),
    ]
    patch_popen.set_stdout(lines)
    with SkillSession("parse-abd-nieuws") as s:
        r1 = s.call("alpha")
        r2 = s.call("beta")
        # Lees stdin VOORDAT __exit__ stdin closet
        stdin_content = FakePopen.instances[0].stdin.getvalue()

    assert r1.text == "first"
    assert r2.text == "second"
    lines_written = [line for line in stdin_content.split("\n") if line]
    assert len(lines_written) == 2
    assert json.loads(lines_written[0])["message"]["content"] == "alpha"
    assert json.loads(lines_written[1])["message"]["content"] == "beta"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_call_on_closed_session_raises(patch_popen: type[FakePopen]) -> None:
    s = SkillSession("parse-abd-nieuws")
    with pytest.raises(RuntimeError):
        s.call("x")


def test_no_result_event_returns_error_skillresult(patch_popen: type[FakePopen]) -> None:
    # Stdout heeft alleen niet-result events, dan EOF
    lines = [
        json.dumps({"type": "system", "subtype": "init"}) + "\n",
        json.dumps({"type": "assistant", "message": {}}) + "\n",
    ]
    patch_popen.set_stdout(lines)
    with SkillSession("parse-abd-nieuws") as s:
        result = s.call("x")

    assert result.is_error is True
    assert result.text == ""
    assert result.error_message is not None


# ---------------------------------------------------------------------------
# Stats accumulator
# ---------------------------------------------------------------------------


def test_stats_accumulate_across_calls(patch_popen: type[FakePopen]) -> None:
    lines = [
        _result_line(
            result="a",
            total_cost_usd=0.01,
            usage={
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        ),
        _result_line(
            result="b",
            total_cost_usd=0.02,
            usage={
                "input_tokens": 30,
                "output_tokens": 40,
                "cache_read_input_tokens": 6000,
                "cache_creation_input_tokens": 100,
            },
        ),
        _result_line(
            result="c",
            total_cost_usd=0.03,
            usage={
                "input_tokens": 50,
                "output_tokens": 60,
                "cache_read_input_tokens": 7000,
                "cache_creation_input_tokens": 0,
            },
        ),
    ]
    patch_popen.set_stdout(lines)
    with SkillSession("parse-abd-nieuws") as s:
        s.call("a")
        s.call("b")
        s.call("c")
        stats = s.stats

    assert stats.calls == 3
    assert stats.cost_usd == pytest.approx(0.06)
    assert stats.input_tokens == 90
    assert stats.output_tokens == 120
    assert stats.cache_read_tokens == 13000
    assert stats.cache_creation_tokens == 100
    assert stats.cache_hits == 2  # call 2 en 3
    assert stats.rate_limited == 0


# ---------------------------------------------------------------------------
# SkillResult dataclass
# ---------------------------------------------------------------------------


def test_skillresult_cache_hit_property() -> None:
    r1 = SkillResult(text="", model="m", cache_read_tokens=CACHE_HIT_THRESHOLD)
    assert r1.cache_hit is True
    r2 = SkillResult(text="", model="m", cache_read_tokens=CACHE_HIT_THRESHOLD - 1)
    assert r2.cache_hit is False

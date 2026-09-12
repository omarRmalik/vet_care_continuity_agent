"""Anthropic client plus the offline fixture harness.

Two jobs:

1. Load ``.env`` so a key sitting in the project file is actually visible to the SDK.
   The SDK reads the process environment, not ``.env`` — without this the file is inert.
2. Record every live response to ``data/fixtures/`` and replay it when
   ``VET_AGENT_OFFLINE=1``. A demo that depends on venue wifi is a demo that fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BASE_DIR / "data" / "fixtures"

log = logging.getLogger("vet_agent.llm")

# Real environment wins over .env — override=False is the default and is what we want.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover - dotenv arrives via uvicorn[standard]
    log.debug("python-dotenv not installed; relying on the process environment")

MODEL = "claude-opus-5"

T = TypeVar("T", bound=BaseModel)

_client = None


class LLMUnavailable(RuntimeError):
    """Raised when we can neither call the API nor replay a fixture."""


def is_offline() -> bool:
    return os.environ.get("VET_AGENT_OFFLINE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def get_client():
    """Construct the SDK client lazily so importing this module never needs a key."""
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


# ----------------------------------------------------------------------- fixtures


def _fixture_path(kind: str, system: str, user: str) -> Path:
    digest = hashlib.sha256("\x00".join([kind, system, user]).encode("utf-8")).hexdigest()
    return FIXTURE_DIR / f"{kind}-{digest[:16]}.json"


def _save_fixture(path: Path, kind: str, output: BaseModel) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "kind": kind,
                "model": MODEL,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "output": output.model_dump(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("recorded fixture %s", path.name)


def _load_fixture(
    path: Path, kind: str, output_model: Type[T], allow_fallback: bool
) -> T:
    """Exact match, else — only where it is safe — the most recent fixture of the kind.

    Falling back is fine for a SOAP or discharge rewrite: the vet edited the note, the
    prompt hash moved, and replaying a near-miss still produces sensible text.

    It is NOT safe for the follow-up intake. A near-miss there means replaying one
    owner conversation's answer into a different one — an unrecognised reply could
    silently return the recorded URGENT turn, and the demo would announce an
    escalation nobody described. Callers that own a conversation opt out.
    """
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        log.info("replaying fixture %s", path.name)
        return output_model(**data["output"])

    candidates = sorted(
        FIXTURE_DIR.glob(f"{kind}-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if candidates and allow_fallback:
        log.warning(
            "offline: no exact fixture for %s (%s); replaying %s",
            kind,
            path.name,
            candidates[0].name,
        )
        data = json.loads(candidates[0].read_text(encoding="utf-8"))
        return output_model(**data["output"])

    if candidates:
        raise LLMUnavailable(
            "Offline mode has no recorded response for that exact reply. Use one of "
            "the suggested replies, or restart with VET_AGENT_OFFLINE=0 to run live."
        )

    raise LLMUnavailable(
        f"VET_AGENT_OFFLINE=1 but no fixture of kind '{kind}' exists in {FIXTURE_DIR}. "
        f"Record one by running with VET_AGENT_OFFLINE=0 and a valid ANTHROPIC_API_KEY."
    )


# --------------------------------------------------------------------- the one call


def parse_structured(
    kind: str,
    system: str,
    user: str,
    output_model: Type[T],
    *,
    effort: str | None = None,
    max_tokens: int = 8000,
    allow_fallback: bool = True,
) -> T:
    """Single structured-output call, recorded on the way out and replayed offline.

    ``kind`` names the seam ("soap", "discharge", "intake") and namespaces the fixture.
    ``allow_fallback`` controls whether an offline near-miss may replay a different
    recording of the same kind — see :func:`_load_fixture`.
    """
    path = _fixture_path(kind, system, user)

    if is_offline():
        return _load_fixture(path, kind, output_model, allow_fallback)

    if not has_api_key():
        raise LLMUnavailable(
            "ANTHROPIC_API_KEY is not set. Put it in vet-agent/.env (the SDK reads the "
            "process environment; this module loads .env for you), or set "
            "VET_AGENT_OFFLINE=1 to replay recorded fixtures."
        )

    kwargs = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_format": output_model,
    }
    # Thinking is on by default on Opus 5 — do not pass a `thinking` parameter.
    if effort is not None:
        kwargs["output_config"] = {"effort": effort}

    import anthropic

    try:
        response = get_client().messages.parse(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable(
            "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in vet-agent/.env "
            "(keys look like 'sk-ant-...')."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("Rate limited by the Anthropic API; retry shortly.") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable(
            "Could not reach the Anthropic API. Set VET_AGENT_OFFLINE=1 to run from "
            "recorded fixtures."
        ) from exc

    if response.stop_reason == "refusal":
        raise LLMUnavailable(f"Model declined the request: {response.stop_details}")

    parsed = response.parsed_output
    if parsed is None:
        raise LLMUnavailable(f"No structured output returned for kind '{kind}'.")

    _save_fixture(path, kind, parsed)
    return parsed

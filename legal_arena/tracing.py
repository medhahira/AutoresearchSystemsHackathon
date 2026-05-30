from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any


def _tracing_requested(enabled: bool | None) -> bool:
    if enabled is not None:
        return enabled
    return os.getenv("LEGAL_ARENA_RAINDROP", "0").lower() in {"1", "true", "yes"} or bool(
        os.getenv("RAINDROP_LOCAL_DEBUGGER")
    )


@dataclass(slots=True)
class RaindropTracer:
    enabled: bool
    interaction: Any = None
    sdk: Any = None
    run_id: str | None = None
    init_error: str | None = None
    event_count: int = 0
    attachment_count: int = 0

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "run_id": self.run_id,
            "init_error": self.init_error,
            "local_debugger": os.getenv("RAINDROP_LOCAL_DEBUGGER"),
            "write_key_configured": bool(os.getenv("RAINDROP_WRITE_KEY")),
            "event_count": self.event_count,
            "attachment_count": self.attachment_count,
        }

    @classmethod
    def start(
        cls,
        *,
        enabled: bool | None,
        event: str,
        input_payload: Any,
        properties: dict[str, Any] | None = None,
    ) -> "RaindropTracer":
        if not _tracing_requested(enabled):
            return cls(enabled=False)

        try:
            import raindrop.analytics as raindrop
        except ImportError as exc:
            return cls(enabled=False, init_error=f"raindrop-ai is not installed: {exc}")

        try:
            write_key = os.getenv("RAINDROP_WRITE_KEY")
            raindrop.init(write_key, tracing_enabled=True, bypass_otel_for_tools=True)
            run_id = f"legal-arena-{uuid.uuid4()}"
            interaction = raindrop.begin(
                user_id=os.getenv("LEGAL_ARENA_TRACE_USER", "local-user"),
                event=event,
                input=_safe_payload(input_payload),
                convo_id=run_id,
                properties=properties or {},
            )
            return cls(enabled=True, interaction=interaction, sdk=raindrop, run_id=run_id)
        except Exception as exc:
            return cls(enabled=False, sdk=raindrop, init_error=str(exc))

    def track_tool(
        self,
        *,
        name: str,
        started: float,
        input_payload: Any | None = None,
        output_payload: Any | None = None,
        error: BaseException | str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or self.interaction is None:
            return
        duration_ms = (time.perf_counter() - started) * 1000
        try:
            kwargs: dict[str, Any] = {
                "name": name,
                "duration_ms": duration_ms,
                "properties": properties or {},
            }
            if input_payload is not None:
                kwargs["input"] = _safe_payload(input_payload)
            if output_payload is not None:
                kwargs["output"] = _safe_payload(output_payload)
            if error is not None:
                kwargs["error"] = error
            self.interaction.track_tool(**kwargs)
        except Exception:
            return

    def add_attachment(
        self,
        *,
        name: str,
        value: Any,
        role: str = "output",
        attachment_type: str = "text",
    ) -> None:
        if not self.enabled or self.interaction is None:
            return
        try:
            self.interaction.add_attachments(
                [
                    {
                        "type": attachment_type,
                        "name": name,
                        "value": _safe_payload(value),
                        "role": role,
                    }
                ]
            )
            self.attachment_count += 1
        except Exception:
            return

    def track_ai_event(
        self,
        *,
        event: str,
        input_payload: Any | None = None,
        output_payload: Any | None = None,
        properties: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        if not self.enabled or self.sdk is None:
            return
        try:
            self.sdk.track_ai(
                user_id=os.getenv("LEGAL_ARENA_TRACE_USER", "local-user"),
                event=event,
                model=os.getenv("LEGAL_ARENA_MODEL"),
                input=_safe_payload(input_payload),
                output=_safe_payload(output_payload),
                convo_id=self.run_id,
                properties=properties or {},
                attachments=attachments or [],
            )
            self.event_count += 1
        except Exception:
            return

    def finish(self, output_payload: Any | None = None, properties: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        try:
            if self.interaction is not None:
                self.interaction.finish(output=_safe_payload(output_payload), properties=properties or {})
            if self.sdk is not None:
                self.sdk.flush()
        except Exception:
            return

    def shutdown(self) -> None:
        if not self.enabled or self.sdk is None:
            return
        try:
            self.sdk.shutdown()
        except Exception:
            return


def _safe_payload(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(indent=2)
    if isinstance(value, str):
        return value[:100_000]
    try:
        import json

        return json.dumps(value, default=str, indent=2)[:100_000]
    except Exception:
        return str(value)[:100_000]
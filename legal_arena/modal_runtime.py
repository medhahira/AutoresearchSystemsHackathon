from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_GPU_LIMITS: dict[str, int] = {
    "A10": 8,
}
ALLOWED_GPUS = frozenset(DEFAULT_GPU_LIMITS)


@dataclass(slots=True)
class ModalRuntimeConfig:
    enabled: bool = False
    app_name: str = "legal-arena"
    gpu: str | None = None
    timeout_s: int = 30 * 60
    workspace_persistence: str = "snapshot_filesystem"
    gpu_limits: dict[str, int] = field(default_factory=lambda: DEFAULT_GPU_LIMITS.copy())

    @classmethod
    def from_env(cls) -> "ModalRuntimeConfig":
        gpu = os.getenv("LEGAL_ARENA_MODAL_GPU") or None
        return cls(
            enabled=os.getenv("LEGAL_ARENA_USE_MODAL", "0").lower() in {"1", "true", "yes"},
            app_name=os.getenv("LEGAL_ARENA_MODAL_APP", "legal-arena"),
            gpu=validate_modal_gpu(gpu),
            timeout_s=int(os.getenv("LEGAL_ARENA_MODAL_TIMEOUT_S", str(30 * 60))),
            workspace_persistence=os.getenv("LEGAL_ARENA_MODAL_WORKSPACE", "snapshot_filesystem"),
        )


def validate_modal_gpu(gpu: str | None) -> str | None:
    if gpu is None:
        return None
    normalized_gpu = gpu.upper()
    if normalized_gpu not in ALLOWED_GPUS:
        allowed = ", ".join(sorted(ALLOWED_GPUS))
        raise ValueError(f"Unsupported Modal GPU '{gpu}'. Legal Arena allows only: {allowed}.")
    return normalized_gpu


def modal_extension_available() -> bool:
    try:
        from agents.extensions.sandbox.modal import ModalSandboxClient, ModalSandboxClientOptions  # noqa: F401
        from agents.sandbox import SandboxRunConfig  # noqa: F401
        from agents.run import RunConfig  # noqa: F401
    except ImportError:
        return False
    return True


def create_modal_sandbox_client():
    from agents.extensions.sandbox.modal import ModalSandboxClient

    return ModalSandboxClient()


def create_modal_sandbox_options(config: ModalRuntimeConfig, *, gpu: str | None = None):
    from agents.extensions.sandbox.modal import ModalSandboxClientOptions

    return ModalSandboxClientOptions(
        app_name=config.app_name,
        workspace_persistence=config.workspace_persistence,
        gpu=gpu or config.gpu,
        timeout=config.timeout_s,
    )


def create_sandbox_run_config(*, client, session):
    from agents.run import RunConfig
    from agents.sandbox import SandboxRunConfig

    return RunConfig(sandbox=SandboxRunConfig(client=client, session=session))
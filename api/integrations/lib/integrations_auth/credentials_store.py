"""Where connected integrations (credentials + account info) are persisted.

VectorShift keeps IntegrationObject in Mongo/Redis. Here: one JSON file per
integration under CREDENTIALS_DIR with mode 600, shared by the UI, CLI and the
MCP server. MemoryCredentialsStore is for tests.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

try:  # POSIX advisory lock; the UI and the MCP server are separate processes sharing these files
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from api.integrations.constants.constants import IntegrationsDisplayToRoute
from api.integrations.models.data_models import IntegrationObject
from api.integrations.models.errors import ConfigError


def _route(name: str) -> str:
    return IntegrationsDisplayToRoute.get(name, name)


class CredentialsStore(Protocol):
    def load(self, integration_type: str) -> IntegrationObject | None: ...

    def save(self, integration: IntegrationObject) -> None: ...

    def delete(self, integration_type: str) -> None: ...


class MemoryCredentialsStore:
    def __init__(self) -> None:
        self._items: dict[str, IntegrationObject] = {}

    def load(self, integration_type: str) -> IntegrationObject | None:
        return self._items.get(_route(integration_type))

    def save(self, integration: IntegrationObject) -> None:
        self._items[_route(integration.integration_type)] = integration

    def delete(self, integration_type: str) -> None:
        self._items.pop(_route(integration_type), None)


class FileCredentialsStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"cannot create credentials dir {self.directory}: {exc}") from exc

    def _path(self, integration_type: str) -> Path:
        return self.directory / f"{_route(integration_type)}.json"

    @contextmanager
    def _locked(self, integration_type: str) -> Iterator[None]:
        """Cross-process exclusive lock on a sidecar file so two processes refreshing the
        same token do not interleave read-modify-write. No-op where fcntl is unavailable."""
        lock_path = self._path(integration_type).with_suffix(".lock")
        if fcntl is None:
            yield
            return
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def load(self, integration_type: str) -> IntegrationObject | None:
        path = self._path(integration_type)
        if not path.exists():
            return None
        try:
            with self._locked(integration_type):
                return IntegrationObject.model_validate_json(path.read_text())
        except (OSError, ValueError) as exc:
            raise ConfigError(f"corrupt credentials file {path}: {exc}") from exc

    def save(self, integration: IntegrationObject) -> None:
        path = self._path(integration.integration_type)
        tmp = path.with_suffix(".tmp")
        try:
            with self._locked(integration.integration_type):
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(integration.model_dump_json(indent=2))
                os.replace(tmp, path)
                os.chmod(path, 0o600)
        except OSError as exc:
            raise ConfigError(f"cannot write credentials file {path}: {exc}") from exc

    def delete(self, integration_type: str) -> None:
        try:
            with self._locked(integration_type):
                self._path(integration_type).unlink(missing_ok=True)
        except OSError as exc:
            raise ConfigError(f"cannot delete credentials file: {exc}") from exc

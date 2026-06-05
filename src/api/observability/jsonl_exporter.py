"""JSONL OpenTelemetry span exporter."""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class JsonlSpanExporter(SpanExporter):
    """Append each finished span as one JSON line."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._file: Any | None = None

    def _open(self) -> Any:
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._path, "a", buffering=1, encoding="utf-8")
        return self._file

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            try:
                handle = self._open()
                for span in spans:
                    handle.write(span.to_json(indent=None) + os.linesep)
                return SpanExportResult.SUCCESS
            except OSError:
                return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.flush()
                    self._file.close()
                finally:
                    self._file = None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            if self._file is not None:
                self._file.flush()
        return True


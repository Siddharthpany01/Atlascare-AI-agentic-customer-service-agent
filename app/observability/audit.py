"""
app/observability/audit.py
---------------------------
AuditLogger — WORM (Write Once Read Many) audit log for AtlasCare.

Each log entry is a single JSON line appended to audit.log.
The file is never truncated, rotated, or overwritten by this module.

Log line schema:
  {
    "timestamp_utc": "2025-05-24T10:24:55.123456Z",
    "trace_id": "trc_20250524102455_a1b2c3d4",
    "event": "QUERY_COMPLETE",
    "payload": { ... }
  }

Failure policy:
  File write errors are logged to stderr but never propagate.
  Observability must never break the happy path.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from app.safety import mask_pii

logger = logging.getLogger(__name__)

_DEFAULT_AUDIT_PATH = "/tmp/audit.log"


class AuditLogger:
    """
    WORM audit logger.

    Thread-safe via a per-instance lock.
    Instantiate via get_audit_logger() singleton.
    """

    def __init__(self, path: str = _DEFAULT_AUDIT_PATH) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        # Ensure parent directory exists — failure is non-fatal
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AuditLogger could not create log directory %s: %s — writes will fail silently",
                self._path.parent, exc,
            )
        logger.info("AuditLogger initialised at %s", self._path)

    def log(self, trace_id: str, event: str, payload: dict) -> None:
        """
        Append one audit entry to the log file.

        Never raises — file errors are caught and logged to stderr.
        """
        entry = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "event": event,
            "payload": payload,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)

        try:
            
            safe_line = mask_pii(line)
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(safe_line + "\n")
            logger.debug("AuditLogger event=%s trace_id=%s", event, trace_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "AuditLogger write failed (trace_id=%s event=%s): %s",
                trace_id, event, exc,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return the module-level AuditLogger singleton."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger

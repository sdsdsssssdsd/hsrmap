"""Windows-safe exclusive lock must reject a second enrichment process."""

from pathlib import Path

import pytest

from hsrmap.detail_lock import EnrichmentLock, LockHeldError


def test_second_lock_is_rejected(tmp_path: Path):
    path = tmp_path / ".lock"
    first = EnrichmentLock(path)
    first.acquire()
    try:
        second = EnrichmentLock(path)
        with pytest.raises(LockHeldError):
            second.acquire()
    finally:
        first.release()

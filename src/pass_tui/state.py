from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .models import ItemRef


class StateStore:
    """Small on-disk state: current rofi stage, last copied item, open item."""

    def __init__(self, cache_dir: Path) -> None:
        self.dir = cache_dir
        self.stage_path = cache_dir / "stage"
        self.last_item_path = cache_dir / "lastitem.json"
        self.stamp_path = cache_dir / "copy.stamp"
        self.open_item_path = cache_dir / "open-item.json"

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get_stage(self) -> str:
        try:
            return self.stage_path.read_text(encoding="utf-8").strip() or "list"
        except OSError:
            return "list"

    def set_stage(self, stage: str) -> None:
        self._write(self.stage_path, stage.encode("utf-8"))

    def get_last_copied(self) -> tuple[ItemRef, float] | None:
        """Return (ref, copy timestamp) atomically-ish, or None.

        Reads the stamp first, then the ref: a concurrent set_last_copied
        always writes the ref before the stamp, so a stamp without a
        matching ref yields None instead of a half-written pair.
        """
        try:
            stamp = int(self.stamp_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        try:
            data = json.loads(self.last_item_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            return ItemRef.from_dict(data), float(stamp)
        except ValueError:
            return None

    def set_last_copied(self, ref: ItemRef, timestamp: float | None = None) -> None:
        self._write(
            self.last_item_path, json.dumps(ref.to_dict()).encode("utf-8")
        )
        self._write(
            self.stamp_path,
            str(int(timestamp if timestamp is not None else time.time())).encode(),
        )

    def clear_last_copied(self) -> None:
        for path in (self.stamp_path, self.last_item_path):
            try:
                path.unlink()
            except OSError:
                pass

    def get_open_item(self) -> ItemRef | None:
        try:
            data = json.loads(self.open_item_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            return ItemRef.from_dict(data)
        except ValueError:
            return None

    def set_open_item(self, ref: ItemRef | None) -> None:
        if ref is None:
            try:
                self.open_item_path.unlink()
            except OSError:
                pass
            return
        self._write(self.open_item_path, json.dumps(ref.to_dict()).encode("utf-8"))

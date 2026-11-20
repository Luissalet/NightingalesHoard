"""Publish staged exports and retain the prior files for agent undo."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from .workbench.engine import DataError
from .workbook_export import sha256_file


class ExportFiles:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()

    def publish(self, stage: Path, target: Path, names: list[str], *, reversible: bool) -> str | None:
        with self.lock, TemporaryDirectory(prefix=".export-prior-", dir=target) as tmp:
            prior = Path(tmp)
            entries = []
            for name in names:
                dest = target / name
                present = dest.exists()
                if present:
                    shutil.copy2(dest, prior / name)
                staged = stage / name
                entries.append({"path": str(dest), "prior": present, "sha256": sha256_file(staged),
                                "mtime_ns": staged.stat().st_mtime_ns})
            token = secrets.token_hex(16) if reversible else None
            history = self.root / token if token else None
            committed = []
            try:
                if history:
                    (history / "prior").mkdir(parents=True)
                    for item in entries:
                        if item["prior"]:
                            name = Path(item["path"]).name
                            shutil.copy2(prior / name, history / "prior" / name)
                    (history / "undo.json").write_text(json.dumps(entries), encoding="utf-8")
                for name in names:
                    (stage / name).replace(target / name)
                    committed.append(name)
            except OSError:
                for name in reversed(committed):
                    self._restore(prior, target / name)
                if history:
                    shutil.rmtree(history, ignore_errors=True)
                raise
            return token

    @staticmethod
    def _restore(prior: Path, target: Path) -> None:
        backup = prior / target.name
        if backup.exists():
            backup.replace(target)
        else:
            target.unlink()

    def undo(self, token: str) -> list[str]:
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise DataError("Invalid export undo token / Identificador de deshacer no válido")
        history = self.root / token
        with self.lock:
            manifest = history / "undo.json"
            if not manifest.is_file():
                raise DataError("Export undo is unavailable / No se puede deshacer esta exportación")
            entries = json.loads(manifest.read_text(encoding="utf-8"))
            targets = [Path(item["path"]) for item in entries]
            for target, item in zip(targets, entries):
                if (not target.is_file() or target.stat().st_mtime_ns != item["mtime_ns"]
                        or sha256_file(target) != item["sha256"]):
                    raise DataError(f"Export file changed / El archivo exportado ha cambiado: {target}")
            with TemporaryDirectory(prefix=".export-undo-", dir=targets[0].parent) as tmp:
                stage = Path(tmp)
                current = stage / "current"
                prior = stage / "prior"
                current.mkdir()
                prior.mkdir()
                for target, item in zip(targets, entries):
                    shutil.copy2(target, current / target.name)
                    if item["prior"]:
                        shutil.copy2(history / "prior" / target.name, prior / target.name)
                restored = []
                try:
                    for target in targets:
                        self._restore(prior, target)
                        restored.append(target)
                    manifest.unlink()
                except OSError:
                    for target in reversed(restored):
                        (current / target.name).replace(target)
                    raise
            shutil.rmtree(history, ignore_errors=True)
            return [str(target) for target in targets]

"""Wiring of the metadata database, the DuckDB engine, and every feature
(ingestion, transforms, quality, charts, dashboards, models, the analysis
log, ask-your-data). Every public method here is what both the HTTP API and
the agent tools call through, and every one of them writes an analysis-log
entry — this is the one place that happens, so neither surface can skip it.

Heavy work (big ingests, model training, chart rendering) runs in a small
thread pool so it never blocks the event loop; each call has a timeout.
"""

from __future__ import annotations

import concurrent.futures
import logging
import secrets
import sqlite3
import threading
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from . import __version__, backend, db
from .hoard_link import fam_web, tokens
from .hoard_link import paths as hl_paths
from .hoard_link.tokens import read_or_create_token
from .config import Config
from .dac import render as dac_render
from .dac import semantic as dac_semantic
from .dac import spec as dac_spec
from .dac import store as dac_store
from .lab import LabError
from .lab import diagnostics as lab_diagnostics
from .lab import drift as lab_drift
from .lab import eda as lab_eda
from .lab import explain as lab_explain
from .lab import optimize as lab_optimize
from .lab import pipeline as lab_pipeline
from .lab import registry as lab_registry
from .lab import report as lab_report_mod
from .lab import tuning as lab_tuning
from . import hoard_source
from .workbench import ask as ask_engine
from .workbench.charts import ChartSpec, render_png, run_chart, to_vega_lite
from .workbench.engine import DataError, Engine, slugify_name
from .workbench import models as model_engine
from .workbench.quality import QualityError, RULE_KINDS, build_check
from .workbench.steps import StepError
from . import workbook_export

log = logging.getLogger("nightingale")

MAX_INGEST_TIMEOUT_S = 300
MAX_MODEL_TIMEOUT_S = 180
MAX_LAB_TIMEOUT_S = 240
MAX_CHART_ROWS_FOR_DF = 1_000_000
#: data folders a user may read from on purpose (the demo files, a file this app exported, a drop folder)
OWN_DATA_FOLDERS = ("files", "exports", "inbox")
MAX_URL_BYTES = 100 * 1024 * 1024  # the largest file a URL ingest or refresh downloads
URL_TIMEOUT_S = 30


def _json_loads(text) -> dict:
    import json

    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class NotFoundError(LookupError):
    pass


def write_token(config: Config) -> str:
    """The MCP token is persistent: created once, reused on every later start (the shared token file helper)."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return read_or_create_token(config.token_path)


def write_url(config: Config) -> None:
    """Tell the MCP bridge where the app listens (the ``url`` file next to the token)."""
    try:
        tokens.write_url(config.url_path, f"http://127.0.0.1:{config.port}")
    except OSError:
        log.warning("could not write %s", config.url_path)


class Services:
    def __init__(self, config: Config, clock: Optional[Callable[[], float]] = None):
        self.config = config
        self.clock = clock or time.time
        self.started_at = self.clock()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        write_url(config)
        self.meta = db.Meta(db.connect(config.data_dir))
        self.engine = Engine(config.duckdb_path)
        self.link = backend.load_link(config.data_dir)
        self.web_fetcher = None  # a hoard_link.web.fetch.Fetcher for the local fallback of URL downloads (tests inject one)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="nightingale-work")
        self._dataset_locks: dict[int, threading.Lock] = {}
        self._dataset_locks_guard = threading.Lock()
        config.exports_dir.mkdir(parents=True, exist_ok=True)
        config.charts_dir.mkdir(parents=True, exist_ok=True)
        if config.demo:
            from .demo import seed_demo

            seed_demo(self)

    def now(self) -> float:
        return self.clock()

    def stop(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.engine.close()
        self.meta.close()

    def status(self) -> dict:
        datasets = self.meta.list_datasets()
        return {
            "service": "nightingale-hoard", "version": __version__, "data_dir": str(self.config.data_dir),
            "datasets": len(datasets), "sources": len(self.meta.list_sources()),
            "models": len(self.meta.list_models()), "started_at": self.started_at,
            "backends": backend.app_backends(),
        }

    # ---- per-dataset write serialization ------------------------------------
    def _dataset_lock(self, dataset_id: int) -> threading.Lock:
        """One lock per dataset id, held across a version-changing operation's
        whole "read current_version -> materialize -> insert version -> update
        current_version" sequence (transform apply, undo/redo, refresh/replay,
        pipeline apply, delete). Two such operations on the *same* dataset used
        to race on `current_version` -- each captured it once before being
        queued onto the worker pool, so two concurrent calls could compute the
        same "next version" and collide (a `versions.dataset_id, version`
        UNIQUE violation), or one could silently discard the other's just-added
        version. This lock makes them queue instead; a different dataset id
        gets its own lock, so it is never blocked, and plain reads (EDA,
        preview, charts, ...) never take this lock at all."""
        with self._dataset_locks_guard:
            lock = self._dataset_locks.get(dataset_id)
            if lock is None:
                lock = threading.Lock()
                self._dataset_locks[dataset_id] = lock
            return lock

    # ---- run heavy work off the event loop, with a timeout -----------------
    def run_heavy(self, fn: Callable, *args, timeout: float = MAX_INGEST_TIMEOUT_S, **kwargs):
        future = self.executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise DataError(f"operation exceeded {timeout:.0f}s and was stopped") from exc

    # ---- logging -------------------------------------------------------
    def _log(self, op: str, source: str, dataset: Optional[str], input_data: Any,
              fn: Callable[[], dict]) -> dict:
        start = time.monotonic()
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.monotonic() - start) * 1000
            self.meta.log(op, source, dataset, input_data, "", False, str(exc), elapsed)
            raise
        elapsed = (time.monotonic() - start) * 1000
        summary = result.get("_log_summary") if isinstance(result, dict) else None
        summary = summary or (str(result)[:300] if not isinstance(result, dict) else "ok")
        op_id = self.meta.log(op, source, dataset, input_data, summary, True, None, elapsed)
        if isinstance(result, dict):
            result = {**result, "log_id": op_id}
            result.pop("_log_summary", None)
        return result

    # ---- datasets/versions helpers -----------------------------------------
    def _dataset_row(self, name_or_id) -> sqlite3.Row:
        row = self.meta.get_dataset(str(name_or_id))
        if row is None and str(name_or_id).isdigit():
            row = self.meta.get_dataset_by_id(int(name_or_id))
        if row is None:
            names = [d["name"] for d in self.meta.list_datasets()]
            raise NotFoundError(f"unknown dataset: {name_or_id!r}. Registered datasets: {names}")
        return row

    def _version_row(self, dataset_row: sqlite3.Row, version: Optional[int] = None) -> sqlite3.Row:
        v = version if version is not None else dataset_row["current_version"]
        row = self.meta.get_version(dataset_row["id"], v)
        if row is None:
            raise NotFoundError(f"dataset {dataset_row['name']!r} has no version {v}")
        return row

    def _refresh_view(self, dataset_row: sqlite3.Row) -> None:
        version = self.meta.get_version(dataset_row["id"], dataset_row["current_version"])
        if version is not None:
            self.engine.set_view(dataset_row["name"], version["table_name"])

    def _resolve_table_by_name(self, name: str) -> str:
        row = self._dataset_row(name)
        return self._version_row(row)["table_name"]

    def dataset_summary(self, dataset_row: sqlite3.Row) -> dict:
        version = self.meta.get_version(dataset_row["id"], dataset_row["current_version"])
        versions = self.meta.list_versions(dataset_row["id"])
        import json as _json

        return {
            "id": dataset_row["id"], "name": dataset_row["name"], "source_id": dataset_row["source_id"],
            "created_at": dataset_row["created_at"], "current_version": dataset_row["current_version"],
            "version_count": len(versions),
            "row_count": version["row_count"] if version else 0,
            "columns": _json.loads(version["columns_json"]) if version else [],
            "updated_at": version["created_at"] if version else dataset_row["created_at"],
        }

    def list_datasets(self) -> dict:
        return {"datasets": [self.dataset_summary(d) for d in self.meta.list_datasets()]}

    def dataset_dependents(self, name: str) -> dict:
        """What would be affected by deleting this dataset: its own charts,
        any dashboard holding one of those charts or a KPI on this dataset,
        and any model trained on it. Used both to show a delete-confirmation
        warning and to block a non-forced delete."""
        import json as _json

        dataset_row = self._dataset_row(name)
        charts = [dict(c) for c in self.meta.list_charts() if c["dataset_id"] == dataset_row["id"]]
        chart_ids = {c["id"] for c in charts}
        dashboards = []
        for d in self.meta.list_dashboards():
            full = self.meta.get_dashboard(d["id"])
            spec = _json.loads(full["spec_json"]) if full else {}
            hits = [item for item in spec.get("items", [])
                    if (item.get("type") == "chart" and item.get("chart_id") in chart_ids)
                    or (item.get("type") == "kpi" and item.get("dataset") == name)]
            if hits:
                dashboards.append({"id": d["id"], "name": d["name"], "items": len(hits)})
        models = [dict(m) for m in self.meta.list_models(dataset_row["id"])]
        return {
            "dataset": name,
            "charts": [{"id": c["id"], "name": c["name"]} for c in charts],
            "dashboards": dashboards,
            "models": [{"id": m["id"], "name": m.get("name") or f"model {m['id']}"} for m in models],
            "has_dependents": bool(charts or dashboards or models),
        }

    def dataset_delete(self, name: str, force: bool = False, source: str = "ui") -> dict:
        """Drop a dataset entirely: every version's DuckDB table, its view,
        and its metadata row (which cascades to versions/quality/charts/models
        via ON DELETE CASCADE). Dashboards aren't touched — dashboard_get()
        already tolerates a missing chart/dataset — but a chart or dashboard
        that depended on this dataset stops working, so unless `force` is
        set, dependents block the delete with a clear list to warn the user."""
        dataset_row = self._dataset_row(name)
        deps = self.dataset_dependents(name)
        if deps["has_dependents"] and not force:
            raise ValueError(
                f"dataset {name!r} has dependents: {len(deps['charts'])} chart(s), "
                f"{len(deps['dashboards'])} dashboard(s), {len(deps['models'])} model(s). "
                "Pass force=true to delete anyway."
            )

        def do():
            with self._dataset_lock(dataset_row["id"]):
                if self.meta.get_dataset_by_id(dataset_row["id"]) is None:
                    return {"ok": True, "deleted": name, "dependents": deps, "_log_summary": f"deleted {name!r}"}
                for v in self.meta.list_versions(dataset_row["id"]):
                    self.engine.drop_table(v["table_name"])
                self.engine.drop_view(name)
                self.meta.delete_dataset(dataset_row["id"])
            return {"ok": True, "deleted": name, "dependents": deps, "_log_summary": f"deleted {name!r}"}

        return self._log("dataset_delete", source, name, {"force": force}, do)

    # ---- ingestion ----------------------------------------------------------
    def _finish_ingest(self, name: str, source_id: Optional[int], select_sql: str, row_count: int,
                        columns: list[dict], op: str, options: dict, extra: dict,
                        probe_table: Optional[str] = None) -> dict:
        dataset_row = self.meta.get_dataset(name)
        if dataset_row is not None:
            # Ingesting under a name that is taken used to rename the new data
            # onto the existing dataset's version-0 table -- dropping it -- and
            # then fail on the version row (HTTP 500), leaving that dataset's
            # original data replaced. Seen live when an assistant retried an
            # ingest. Refuse before touching anything.
            if probe_table:
                self.engine.drop_table(probe_table)
            raise DataError(
                f"a dataset named {name!r} already exists. Use data_refresh to reload its source, "
                "pick another name, or delete it first."
            )
        dataset_id = self.meta.add_dataset(name, source_id)
        table_name = f"ds_{dataset_id}_v0"
        if probe_table:
            # the data is already materialized (ingestion scanned the source once); just rename it
            self.engine.rename_table(probe_table, table_name)
        else:
            row_count, columns = self.engine.materialize(table_name, select_sql)
        self.meta.add_version(dataset_id, 0, None, op, options, select_sql, table_name, row_count, columns)
        self.meta.set_current_version(dataset_id, 0)
        row = self.meta.get_dataset_by_id(dataset_id)
        self.engine.set_view(name, table_name)
        summary = self.dataset_summary(row)
        summary["_log_summary"] = f"ingested {name!r}: {row_count} rows, {len(columns)} columns"
        summary.update(extra)
        return summary

    def ingest_file(self, path: str, name: Optional[str] = None, options: Optional[dict] = None,
                     source: str = "ui") -> dict:
        options = dict(options or {})
        p = Path(hl_paths.clean_user_path(path)).expanduser()  # pasted quotes of any kind ("Copy as path") are removed
        if not p.is_absolute():
            p = (self.config.data_dir / p).resolve()
        if not p.exists():
            raise DataError(f"path does not exist: {p}")
        if p.is_file():
            problem = hl_paths.unsafe_file(p, data_dir=self.config.data_dir, allow_data_subdir=OWN_DATA_FOLDERS, lang="en")
            if problem:
                raise DataError(problem)
        suffix = p.suffix.lower()

        def do():
            dataset_name = slugify_name(name or p.stem)
            workbook_snapshot = workbook_export.inventory(p) if suffix in (".xlsx", ".xlsm") else None
            source_options = {**options, **({"workbook_snapshot": workbook_snapshot} if workbook_snapshot else {})}
            source_id = self.meta.add_source(dataset_name, suffix.lstrip("."), str(p), source_options)
            if suffix in (".csv", ".tsv", ".txt"):
                result = self.engine.ingest_delimited(p, options, source_col=options.get("source_column"))
                return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                            result.columns, "ingest", options, result.extra,
                                            probe_table=result.table_name)
            if suffix == ".parquet":
                result = self.engine.ingest_parquet(p)
                return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                            result.columns, "ingest", options, result.extra,
                                            probe_table=result.table_name)
            if suffix in (".json", ".ndjson", ".jsonl"):
                result = self.engine.ingest_json(p, options)
                return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                            result.columns, "ingest", options, result.extra,
                                            probe_table=result.table_name)
            if suffix in (".xlsx", ".xlsm"):
                sheets = self.engine.ingest_excel(p, options)
                if workbook_snapshot and workbook_export.sha256_file(p) != workbook_snapshot["sha256"]:
                    for _, result in sheets:
                        self.engine.drop_table(result.table_name)
                    raise DataError("source workbook changed while it was being ingested; ingest it again")
                out = []
                for sheet_name, result in sheets:
                    dname = slugify_name(f"{dataset_name}__{sheet_name}") if len(sheets) > 1 else dataset_name
                    sheet_options = {**options, "sheet": sheet_name,
                                     "skip_rows": options.get("skip_rows", result.extra.get("skip_rows_detected", 0))}
                    sheet_extra = dict(result.extra)
                    if workbook_snapshot:
                        sheet_extra["workbook_snapshot"] = {"sha256": workbook_snapshot["sha256"],
                                                             "bytes": workbook_snapshot["bytes"],
                                                             "sheet_count": workbook_snapshot["sheet_count"],
                                                             "package_part_count": workbook_snapshot["package_part_count"]}
                    out.append(self._finish_ingest(dname, source_id, result.select_sql, result.row_count,
                                                     result.columns, "ingest", sheet_options, sheet_extra,
                                                     probe_table=result.table_name))
                return out[0] if len(out) == 1 else {"datasets": out,
                                                        "_log_summary": f"ingested {len(out)} sheet(s) from {p.name}"}
            if suffix in (".sqlite", ".db", ".sqlite3"):
                tables = self.engine.ingest_sqlite(p, options)
                out = []
                for table_name_src, result in tables:
                    dname = slugify_name(f"{dataset_name}__{table_name_src}") if len(tables) > 1 else dataset_name
                    out.append(self._finish_ingest(dname, source_id, result.select_sql, result.row_count,
                                                     result.columns, "ingest", {**options, "table": table_name_src}, result.extra,
                                                     probe_table=result.table_name))
                return out[0] if len(out) == 1 else {"datasets": out,
                                                        "_log_summary": f"ingested {len(out)} table(s) from {p.name}"}
            raise DataError(f"unsupported file type: {suffix or '(none)'}")

        return self._log("ingest", source, name, {"path": str(p), "options": options},
                          lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

    def ingest_folder(self, path: str, name: Optional[str] = None, glob: str = "*.csv", source: str = "ui") -> dict:
        p = Path(hl_paths.clean_user_path(path)).expanduser()
        if not p.is_absolute():
            p = (self.config.data_dir / p).resolve()
        if not p.is_dir():
            raise DataError(f"not a folder: {p}")
        problem = hl_paths.unsafe_folder(p, data_dir=self.config.data_dir, allow_data_subdir=OWN_DATA_FOLDERS, lang="en")
        if problem:
            raise DataError(problem)

        def do():
            dataset_name = slugify_name(name or p.name)
            source_id = self.meta.add_source(dataset_name, "folder", str(p), {"glob": glob})
            result = self.engine.ingest_folder(p, glob)
            return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                        result.columns, "ingest", {"glob": glob}, result.extra,
                                        probe_table=result.table_name)

        return self._log("ingest", source, name, {"path": str(p), "glob": glob},
                          lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

    def _download(self, url: str, dest: Path) -> None:
        """Save what ``url`` serves at ``dest``: through the hub when it answers, else with the shared polite fetcher. Either way
        private and loopback addresses are refused (also after a redirect), the download is capped at ``MAX_URL_BYTES`` and a
        failure says why."""
        if not url.startswith(("http://", "https://")):
            raise DataError("only http(s) URLs are supported")
        res = fam_web.fetch_or_local(url, local_fetcher=self.web_fetcher, accept="any", max_bytes=MAX_URL_BYTES,
                                     respect_robots=False, timeout=URL_TIMEOUT_S)
        if not res.get("ok"):
            raise DataError(f"could not download {url}: {res.get('error') or 'unknown error'}")
        if res.get("truncated"):
            raise DataError(f"{url} is larger than {MAX_URL_BYTES // (1024 * 1024)} MB; download it and ingest the file instead")
        body = res.get("body")
        if body is None and res.get("body_omitted"):  # a body the hub does not inline: it saves it in a folder we chose
            big = fam_web.fetch_file(url, dest_dir=str(dest.parent), max_bytes=MAX_URL_BYTES, timeout=120)
            if not big.get("ok") or not big.get("path"):
                raise DataError(f"could not download {url}: {big.get('error') or 'unknown error'}")
            shutil.move(str(big["path"]), str(dest))
            return
        if body is None:
            raise DataError(f"{url} returned no content")
        dest.write_bytes(body)

    def ingest_url(self, url: str, name: Optional[str] = None, fmt: str = "csv", options: Optional[dict] = None,
                    source: str = "ui") -> dict:
        options = dict(options or {})
        if not url.startswith(("http://", "https://")):
            raise DataError("only http(s) URLs are supported")

        def do():
            dataset_name = slugify_name(name or url.rsplit("/", 1)[-1].split("?")[0] or "url_dataset")
            suffix = ".json" if fmt == "json" else ".csv"
            tmp_path = self.engine.cache_dir / f"__url_{secrets.token_hex(6)}{suffix}"
            try:
                self._download(url, tmp_path)
                source_id = self.meta.add_source(dataset_name, f"url_{fmt}", url, options)
                if fmt == "json":
                    result = self.engine.ingest_json(tmp_path, options)
                else:
                    result = self.engine.ingest_delimited(tmp_path, options)
                return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                            result.columns, "ingest", {**options, "url": url}, result.extra,
                                            probe_table=result.table_name)
            finally:
                tmp_path.unlink(missing_ok=True)

        return self._log("ingest", source, name, {"url": url, "fmt": fmt},
                          lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

    def ingest_text(self, text: str, name: str, fmt: str = "csv", options: Optional[dict] = None,
                     source: str = "ui") -> dict:
        options = dict(options or {})

        def do():
            dataset_name = slugify_name(name)
            source_id = self.meta.add_source(dataset_name, f"paste_{fmt}", "(pasted text)", options)
            result = self.engine.ingest_pasted_text(text, fmt, options)
            return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                        result.columns, "ingest", options, result.extra,
                                        probe_table=result.table_name)

        return self._log("ingest", source, name, {"fmt": fmt, "chars": len(text)}, do)

    def ingest_hoard(self, app: Optional[str] = None, tool: Optional[str] = None, args: Optional[dict] = None,
                      list_path: Optional[str] = None, name: Optional[str] = None, preset: Optional[str] = None,
                      options: Optional[dict] = None, source: str = "ui") -> dict:
        """Ingest the records another app returns from one of its tools (called through the hub).

        A dataset that already came from the same app and tool gets a NEW VERSION (op "ingest") on top of its
        current one, so undo returns to the previous snapshot; any other taken name is refused, as for every ingest."""
        spec = hoard_source.resolve_spec(preset, app, tool, args, list_path, options)
        base = hoard_source.PRESETS.get(preset or "", {})
        dataset_name = slugify_name(name or base.get("name") or f"{spec['app']}_{spec['tool']}")

        def do():
            fetched = hoard_source.fetch(spec)
            rows = fetched["rows"]
            if not rows:
                raise DataError(f"{spec['app']}.{spec['tool']} returned no records")
            tmp_path = self.engine.cache_dir / f"__hoard_{secrets.token_hex(6)}.ndjson"
            import json as _json

            tmp_path.write_text("\n".join(_json.dumps(r, ensure_ascii=False, default=str) for r in rows), encoding="utf-8")
            try:
                result = self.engine.ingest_json(tmp_path, {})
            finally:
                tmp_path.unlink(missing_ok=True)
            used = {**spec, "list_path": fetched["list_path"] or spec["list_path"]}
            extra = {"from": f"hoard://{spec['app']}/{spec['tool']}", "list_path": fetched["list_path"], "pages": fetched["pages"],
                     "truncated": fetched["truncated"], "unverified": bool(base) and not base.get("verified", True)}
            existing = self.meta.get_dataset(dataset_name)
            existing_source = self.meta.get_source(existing["source_id"]) if existing and existing["source_id"] else None
            if existing is not None and existing_source is not None and existing_source["kind"] == "hoard":
                old = _json_loads(existing_source["options_json"])
                if (old.get("app"), old.get("tool")) == (spec["app"], spec["tool"]):
                    return self._add_ingest_version(existing, result, used, extra)
            source_id = self.meta.add_source(dataset_name, "hoard", f"hoard://{spec['app']}/{spec['tool']}", used)
            return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count, result.columns,
                                        "ingest", used, extra, probe_table=result.table_name)

        return self._log("ingest", source, dataset_name, {"hoard": {k: spec[k] for k in ("app", "tool", "args", "list_path")}},
                          lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

    def _add_ingest_version(self, dataset_row: sqlite3.Row, result, used: dict, extra: dict) -> dict:
        """A re-run of a hoard ingest: the fresh records become the next version of the same dataset."""
        with self._dataset_lock(dataset_row["id"]):
            fresh = self.meta.get_dataset_by_id(dataset_row["id"])
            if fresh is None:
                self.engine.drop_table(result.table_name)
                raise NotFoundError(f"dataset {dataset_row['name']!r} no longer exists")
            current = self._version_row(fresh)
            for dropped in self.meta.delete_versions_after(fresh["id"], current["version"]):
                self.engine.drop_table(dropped["table_name"])
            new_version = max(r["version"] for r in self.meta.list_versions(fresh["id"])) + 1
            table = f"ds_{fresh['id']}_v{new_version}"
            self.engine.rename_table(result.table_name, table)
            self.meta.add_version(fresh["id"], new_version, current["version"], "ingest", used, result.select_sql, table,
                                   result.row_count, result.columns)
            self.meta.set_current_version(fresh["id"], new_version)
            if fresh["source_id"]:
                self.meta.touch_source(fresh["source_id"])
            row = self.meta.get_dataset_by_id(fresh["id"])
        self._refresh_view(row)
        summary = self.dataset_summary(row)
        summary.update(extra)
        summary["new_version"] = new_version
        summary["_log_summary"] = f"re-ingested {row['name']!r} from {extra['from']}: v{new_version}, {result.row_count} rows"
        return summary

    def refresh(self, dataset_name: str, source: str = "ui", check_quality: bool = True) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        source_row = self.meta.get_source(dataset_row["source_id"]) if dataset_row["source_id"] else None
        if source_row is None:
            raise DataError(f"dataset {dataset_name!r} has no re-ingestable source")

        def do():
            import json as _json

            with self._dataset_lock(dataset_row["id"]):
                fresh = self.meta.get_dataset_by_id(dataset_row["id"])
                if fresh is None:
                    raise NotFoundError(f"dataset {dataset_row['name']!r} no longer exists")
                options = _json.loads(source_row["options_json"] or "{}")
                kind, path = source_row["kind"], source_row["path"]
                p = Path(path)
                # capture the recorded recipe (every step up to the current tip) before wiping history --
                # `fresh`, re-read under the lock, so a step applied while this call was queued isn't lost
                old_steps = [{"op": v["op"], "params": _json.loads(v["params_json"])}
                              for v in self.meta.list_versions(fresh["id"])
                              if v["op"] != "ingest" and v["version"] <= fresh["current_version"]]
                if kind in ("csv", "tsv", "txt"):
                    result = self.engine.ingest_delimited(p, options)
                elif kind == "parquet":
                    result = self.engine.ingest_parquet(p)
                elif kind in ("json", "ndjson", "jsonl"):
                    result = self.engine.ingest_json(p, options)
                elif kind == "hoard":
                    fetched = hoard_source.fetch(hoard_source.resolve_spec(None, options.get("app"), options.get("tool"), options.get("args"),
                                                                            options.get("list_path"), options))
                    if not fetched["rows"]:
                        raise DataError("the source app returned no records")
                    tmp_path = self.engine.cache_dir / f"__refresh_{secrets.token_hex(6)}.ndjson"
                    tmp_path.write_text("\n".join(_json.dumps(r, ensure_ascii=False, default=str) for r in fetched["rows"]), encoding="utf-8")
                    try:
                        result = self.engine.ingest_json(tmp_path, {})
                    finally:
                        tmp_path.unlink(missing_ok=True)
                elif kind.startswith("url_"):
                    fmt = "json" if kind.endswith("json") else "csv"
                    tmp_path = self.engine.cache_dir / f"__refresh_{secrets.token_hex(6)}.{fmt}"
                    try:
                        self._download(path, tmp_path)
                        result = self.engine.ingest_json(tmp_path, options) if fmt == "json" \
                            else self.engine.ingest_delimited(tmp_path, options)
                    finally:
                        tmp_path.unlink(missing_ok=True)
                else:
                    raise DataError(f"source kind {kind!r} cannot be refreshed directly; re-ingest it manually")

                # wipe the old version history (dropping its tables) and rebuild from the fresh raw data
                old_versions = self.meta.delete_all_versions(fresh["id"])
                for v in old_versions:
                    self.engine.drop_table(v["table_name"])
                new_table = f"ds_{fresh['id']}_v0"
                row_count, columns = result.row_count, result.columns
                self.engine.rename_table(result.table_name, new_table)
                self.meta.add_version(fresh["id"], 0, None, "ingest", options, result.select_sql,
                                       new_table, row_count, columns)
                self.meta.set_current_version(fresh["id"], 0)
                self.meta.touch_source(source_row["id"])
                # replay the recorded recipe (every step after the original ingest) on the fresh raw data
                replayed = 0
                current_version = 0
                for step in old_steps:
                    new_version = current_version + 1
                    prev_table = self.meta.get_version(fresh["id"], current_version)["table_name"]
                    new_table_v = f"ds_{fresh['id']}_v{new_version}"
                    try:
                        select_sql, rc, cols = self.engine.apply_step(prev_table, new_table_v, step["op"], step["params"],
                                                                        self._resolve_table_by_name)
                    except (DataError, StepError):
                        break  # a step no longer applies to the refreshed shape: stop replay here, keep what worked
                    self.meta.add_version(fresh["id"], new_version, current_version, step["op"],
                                            step["params"], select_sql, new_table_v, rc, cols)
                    self.meta.set_current_version(fresh["id"], new_version)
                    current_version = new_version
                    replayed += 1
                row = self.meta.get_dataset_by_id(fresh["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"refreshed {dataset_row['name']!r}: {row_count} raw rows, replayed {replayed} step(s)"
            summary["steps_replayed"] = replayed
            if check_quality and self.meta.list_rules(row["id"]):
                try:
                    quality = self.quality_run(row["name"], source=source)
                    failures = [r for r in quality["results"] if not r["passed"]]
                    summary["quality"] = {
                        "version": quality["version"],
                        "checked_rules": len(quality["results"]),
                        "passed_rules": len(quality["results"]) - len(failures),
                        "failed_rules": [{"rule_id": r["rule_id"], "name": r["name"], "kind": r["kind"],
                                          "failed": r["failed"], "sample": r["sample"][:3],
                                          "message": r["message"]} for r in failures],
                    }
                except (DataError, StepError, QualityError) as exc:
                    summary["quality"] = {"error": str(exc), "checked_rules": 0}
            return summary

        return self._log("refresh", source, dataset_row["name"], {"check_quality": check_quality},
                         lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

    # ---- profile / preview / query ------------------------------------------
    def profile(self, dataset_name: str, version: Optional[int] = None) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        v = self._version_row(dataset_row, version)
        import json as _json

        columns = _json.loads(v["columns_json"])
        prof = self.engine.profile_table(v["table_name"], columns, v["row_count"])
        return {"dataset": dataset_row["name"], "version": v["version"], "row_count": v["row_count"],
                "columns": columns, "profile": prof}

    def correlation(self, dataset_name: str) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        v = self._version_row(dataset_row)
        import json as _json

        columns = [c["name"] for c in _json.loads(v["columns_json"])
                   if c["type"].upper().split("(")[0] in
                   ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "REAL")]
        if len(columns) < 2:
            raise DataError("need at least 2 numeric columns for a correlation matrix")
        return self.engine.correlation_matrix(v["table_name"], columns)

    def preview(self, dataset_name: str, version: Optional[int] = None, limit: int = 50) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        v = self._version_row(dataset_row, version)
        result = self.engine.query(f'SELECT * FROM "{v["table_name"]}"', limit=limit)
        result.update({"dataset": dataset_row["name"], "version": v["version"], "row_count_total": v["row_count"]})
        return result

    def query(self, sql: str, limit: int = 200, source: str = "ui") -> dict:
        return self._log("query", source, None, {"sql": sql[:500]}, lambda: self._run_query(sql, limit))

    def _run_query(self, sql: str, limit: int) -> dict:
        from .sqlgate import gate_sql

        gate_sql(sql)
        result = self.engine.query(sql, limit=limit)
        result["_log_summary"] = f"{result['row_count']} row(s)"
        return result

    # ---- transforms ----------------------------------------------------------
    def transform_preview(self, dataset_name: str, op: str, params: dict, n: int = 20) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        v = self._version_row(dataset_row)
        return self.engine.preview_step(v["table_name"], op, params, self._resolve_table_by_name, n=n)

    def transform_apply(self, dataset_name: str, op: str, params: dict, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            with self._dataset_lock(dataset_row["id"]):
                fresh = self.meta.get_dataset_by_id(dataset_row["id"])
                if fresh is None:
                    raise NotFoundError(f"dataset {dataset_row['name']!r} no longer exists")
                v = self._version_row(fresh)
                new_version = max(r["version"] for r in self.meta.list_versions(fresh["id"])) + 1
                new_table = f"ds_{fresh['id']}_v{new_version}"
                # a new step branching off a version that is not the tip discards the redo stack it replaces
                dropped = self.meta.delete_versions_after(fresh["id"], v["version"])
                for d in dropped:
                    self.engine.drop_table(d["table_name"])
                select_sql, row_count, columns = self.engine.apply_step(
                    v["table_name"], new_table, op, params, self._resolve_table_by_name
                )
                self.meta.add_version(fresh["id"], new_version, v["version"], op, params, select_sql,
                                        new_table, row_count, columns)
                self.meta.set_current_version(fresh["id"], new_version)
                row = self.meta.get_dataset_by_id(fresh["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"{op} on {dataset_row['name']!r} -> v{new_version} ({row_count} rows)"
            return summary

        return self._log("transform", source, dataset_row["name"], {"op": op, "params": params}, do)

    def undo(self, dataset_name: str, steps: int = 1, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            with self._dataset_lock(dataset_row["id"]):
                fresh = self.meta.get_dataset_by_id(dataset_row["id"])
                if fresh is None:
                    raise NotFoundError(f"dataset {dataset_row['name']!r} no longer exists")
                target = max(0, fresh["current_version"] - steps)
                if self.meta.get_version(fresh["id"], target) is None:
                    raise DataError(f"no version {target} to undo to")
                self.meta.set_current_version(fresh["id"], target)
                row = self.meta.get_dataset_by_id(fresh["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"undo {dataset_row['name']!r} to v{target}"
            return summary

        return self._log("undo", source, dataset_row["name"], {"steps": steps}, do)

    def redo(self, dataset_name: str, steps: int = 1, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            with self._dataset_lock(dataset_row["id"]):
                fresh = self.meta.get_dataset_by_id(dataset_row["id"])
                if fresh is None:
                    raise NotFoundError(f"dataset {dataset_row['name']!r} no longer exists")
                versions = [v["version"] for v in self.meta.list_versions(fresh["id"])]
                target = fresh["current_version"] + steps
                if target not in versions:
                    raise DataError(f"no version {target} to redo to (latest is v{max(versions)})")
                self.meta.set_current_version(fresh["id"], target)
                row = self.meta.get_dataset_by_id(fresh["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"redo {dataset_row['name']!r} to v{target}"
            return summary

        return self._log("redo", source, dataset_row["name"], {"steps": steps}, do)

    def recipe(self, dataset_name: str, action: str = "show") -> dict:
        dataset_row = self._dataset_row(dataset_name)
        versions = self.meta.list_versions(dataset_row["id"])
        import json as _json

        steps = [{"version": v["version"], "op": v["op"], "params": _json.loads(v["params_json"]),
                   "row_count": v["row_count"], "created_at": v["created_at"]}
                  for v in versions if v["version"] <= dataset_row["current_version"]]
        if action == "show":
            return {"dataset": dataset_row["name"], "current_version": dataset_row["current_version"], "steps": steps}
        if action == "export":
            lines = [f"-- Recipe for dataset '{dataset_row['name']}' (v0..v{dataset_row['current_version']})",
                      f"-- Exported {db.now_iso()}"]
            for v in versions:
                if v["version"] > dataset_row["current_version"]:
                    continue
                lines.append(f"\n-- step {v['version']}: {v['op']}")
                lines.append(f"CREATE OR REPLACE VIEW nightingale_v{v['version']} AS {v['sql']};")
            return {"dataset": dataset_row["name"], "sql_script": "\n".join(lines)}
        if action == "replay":
            return self.refresh(dataset_row["name"])
        raise DataError(f"unknown recipe action: {action}; choose show/export/replay")

    def lineage(self, dataset_name: str) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        source_row = self.meta.get_source(dataset_row["source_id"]) if dataset_row["source_id"] else None
        return {
            "dataset": dataset_row["name"],
            "source": dict(source_row) if source_row else None,
            **self.recipe(dataset_row["name"], "show"),
        }

    def join_preview(self, dataset_name: str, other_dataset: str, on: list[dict], how: str = "left") -> dict:
        left_table = self._resolve_table_by_name(dataset_name)
        right_table = self._resolve_table_by_name(other_dataset)
        return self.engine.join_match_rate(left_table, right_table, on)

    # ---- quality ---------------------------------------------------------
    def quality_define(self, dataset_name: str, name: str, kind: str, params: dict, source: str = "ui") -> dict:
        if kind not in RULE_KINDS:
            raise QualityError(f"unknown rule kind: {kind}; choose from {RULE_KINDS}")
        dataset_row = self._dataset_row(dataset_name)

        def do():
            table = self._version_row(dataset_row)["table_name"]
            build_check(kind, table, params)  # validate params eagerly
            rule_id = self.meta.add_rule(dataset_row["id"], name, kind, params)
            return {"rule_id": rule_id, "dataset": dataset_row["name"], "name": name, "kind": kind, "params": params,
                    "_log_summary": f"defined rule {name!r} ({kind}) on {dataset_row['name']!r}"}

        return self._log("quality_define", source, dataset_row["name"], {"name": name, "kind": kind, "params": params}, do)

    def quality_run(self, dataset_name: str, rule_id: Optional[int] = None, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            import json as _json

            v = self._version_row(dataset_row)
            rules = [self.meta.get_rule(rule_id)] if rule_id else list(self.meta.list_rules(dataset_row["id"]))
            rules = [r for r in rules if r is not None]
            if not rules:
                raise QualityError("no quality rules defined for this dataset")
            results = []
            for rule in rules:
                params = _json.loads(rule["params_json"])
                check = build_check(rule["kind"], v["table_name"], params)
                if check.scalar_sql is not None:
                    value = self.engine.query(check.scalar_sql, limit=1, cell_cap=None)["rows"][0]
                    value = list(value.values())[0]
                    passed = bool(check.scalar_ok(value))
                    checked, failed, sample = 1, 0 if passed else 1, [{"value": value}]
                else:
                    failing = self.engine.query(check.failing_sql, limit=20, cell_cap=200)
                    checked = v["row_count"]
                    failed = failing["row_count"] if failing["row_count"] < 20 else \
                        self.engine.query(f"SELECT COUNT(*) AS n FROM ({check.failing_sql}) __f", limit=1)["rows"][0]["n"]
                    passed = failed == 0
                    sample = failing["rows"][:10]
                message = "passed" if passed else f"{failed} failing row(s)"
                extra: dict = {}
                if rule["kind"] == "unique" and not passed:
                    # `failed` counts every row of a duplicated group, both
                    # copies of a pair included. Asked "how many duplicate
                    # rows?", an assistant read 12 failing rows as 12
                    # duplicates (and 1,494 left) when 6 extra copies make
                    # 1,500: say both numbers so nobody has to infer them.
                    from .workbench.quality import _col_list, q as _q
                    part = ", ".join(_q(c) for c in _col_list(params))
                    agg = self.engine.query(
                        f"SELECT COUNT(*) AS g, COALESCE(SUM(n), 0) AS r FROM (SELECT COUNT(*) AS n FROM "
                        f"{_q(v['table_name'])} GROUP BY {part} HAVING COUNT(*) > 1) __d", limit=1)["rows"][0]
                    groups, in_groups = int(agg["g"]), int(agg["r"])
                    extra = {"duplicate_groups": groups, "extra_copies": in_groups - groups,
                             "rows_if_deduplicated": checked - (in_groups - groups)}
                    message = (f"{failed} rows share their values with another row: {groups} duplicate group(s), "
                               f"{in_groups - groups} extra cop(ies); {checked - (in_groups - groups)} rows after "
                               f"keeping one of each")
                self.meta.add_result(rule["id"], dataset_row["id"], v["version"], passed, checked, failed, sample, message)
                results.append({"rule_id": rule["id"], "name": rule["name"], "kind": rule["kind"], "passed": passed,
                                  "checked": checked, "failed": failed, **extra, "sample": sample, "message": message})
            n_failed = sum(1 for r in results if not r["passed"])
            return {"dataset": dataset_row["name"], "version": v["version"], "results": results,
                     "_log_summary": f"{len(results) - n_failed}/{len(results)} rule(s) passed"}

        return self._log("quality_run", source, dataset_row["name"], {"rule_id": rule_id}, do)

    def quality_report(self, dataset_name: str) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        import json as _json

        rules = self.meta.list_rules(dataset_row["id"])
        out = []
        for rule in rules:
            latest = self.meta.list_results(dataset_row["id"], rule["id"], limit=1)
            out.append({"rule_id": rule["id"], "name": rule["name"], "kind": rule["kind"],
                          "params": _json.loads(rule["params_json"]),
                          "last_result": dict(latest[0]) if latest else None})
        return {"dataset": dataset_row["name"], "rules": out}

    def quality_delete(self, rule_id: int, source: str = "ui") -> dict:
        rule = self.meta.get_rule(rule_id)
        if rule is None:
            raise NotFoundError(f"unknown rule: {rule_id}")
        self.meta.delete_rule(rule_id)
        return {"ok": True, "rule_id": rule_id}

    # ---- charts / dashboards ------------------------------------------------
    def chart_create(self, dataset_name: str, kind: str, x: Optional[str] = None, y: Optional[str] = None,
                      agg: str = "sum", color: Optional[str] = None, filter: Optional[str] = None,
                      title: str = "", name: Optional[str] = None, image: bool = False,
                      source: str = "ui", lang: str = "en") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            table = self._version_row(dataset_row)["table_name"]
            spec = ChartSpec(kind=kind, dataset=dataset_row["name"], x=x, y=y, agg=agg, color=color,
                              filter=filter, title=title or f"{dataset_row['name']}: {kind}")
            data = run_chart(self.engine, table, spec)
            chart_id = self.meta.add_chart(name or spec.title, dataset_row["id"], spec.to_dict())
            out = {"chart_id": chart_id, "name": name or spec.title, "spec": spec.to_dict(),
                    "vega_lite": to_vega_lite(spec, data), "row_count": data["row_count"],
                    "_log_summary": f"chart {kind} on {dataset_row['name']!r} ({data['row_count']} rows)"}
            if image:
                png_path = self.config.charts_dir / f"chart_{chart_id}.png"
                png_bytes = render_png(spec, data, lang=lang, out_path=png_path)
                out["image_path"] = str(png_path)
                out["image_base64"] = __import__("base64").b64encode(png_bytes).decode("ascii")
            return out

        return self._log("chart", source, dataset_row["name"],
                          {"kind": kind, "x": x, "y": y, "agg": agg, "color": color}, do)

    def chart_get(self, chart_id: int, image: bool = False, lang: str = "en") -> dict:
        row = self.meta.get_chart(chart_id)
        if row is None:
            raise NotFoundError(f"unknown chart: {chart_id}")
        import json as _json

        spec = ChartSpec.from_dict(_json.loads(row["spec_json"]))
        dataset_row = self.meta.get_dataset_by_id(row["dataset_id"])
        table = self._version_row(dataset_row)["table_name"]
        data = run_chart(self.engine, table, spec)
        out = {"chart_id": chart_id, "name": row["name"], "spec": spec.to_dict(),
               "vega_lite": to_vega_lite(spec, data), "row_count": data["row_count"]}
        if image:
            png_bytes = render_png(spec, data, lang=lang)
            out["image_base64"] = __import__("base64").b64encode(png_bytes).decode("ascii")
        return out

    def chart_list(self) -> dict:
        return {"charts": [dict(c) for c in self.meta.list_charts()]}

    def chart_delete(self, chart_id: int) -> dict:
        if self.meta.get_chart(chart_id) is None:
            raise NotFoundError(f"unknown chart: {chart_id}")
        self.meta.delete_chart(chart_id)
        return {"ok": True}

    def dashboard_create(self, name: str, items: Optional[list] = None, filter: Optional[dict] = None,
                          source: str = "ui") -> dict:
        spec = {"items": items or [], "filter": filter or {}}
        dashboard_id = self.meta.add_dashboard(name, spec)
        return self._log("dashboard", source, None, {"action": "create", "name": name},
                          lambda: {"dashboard_id": dashboard_id, "name": name, "spec": spec, "_log_summary": f"created {name!r}"})

    def dashboard_add(self, dashboard_id: int, item: dict, source: str = "ui") -> dict:
        row = self.meta.get_dashboard(dashboard_id)
        if row is None:
            raise NotFoundError(f"unknown dashboard: {dashboard_id}")
        import json as _json

        spec = _json.loads(row["spec_json"])
        spec.setdefault("items", []).append(item)
        self.meta.update_dashboard(dashboard_id, spec)
        return self._log("dashboard", source, None, {"action": "add", "dashboard_id": dashboard_id, "item": item},
                          lambda: {"dashboard_id": dashboard_id, "spec": spec, "_log_summary": "item added"})

    def dashboard_get(self, dashboard_id: int) -> dict:
        row = self.meta.get_dashboard(dashboard_id)
        if row is None:
            raise NotFoundError(f"unknown dashboard: {dashboard_id}")
        import json as _json

        spec = _json.loads(row["spec_json"])
        rendered_items = []
        for item in spec.get("items", []):
            if item.get("type") == "chart":
                try:
                    rendered_items.append({**item, "chart": self.chart_get(item["chart_id"])})
                except NotFoundError:
                    rendered_items.append({**item, "chart": None})
            elif item.get("type") == "kpi":
                try:
                    table = self._resolve_table_by_name(item["dataset"])
                    val = self.engine.query(f"SELECT {item['expr']} AS v FROM \"{table}\"", limit=1, cell_cap=None)
                    rendered_items.append({**item, "value": val["rows"][0]["v"] if val["rows"] else None})
                except Exception as exc:  # noqa: BLE001
                    rendered_items.append({**item, "value": None, "error": str(exc)})
        return {"dashboard_id": dashboard_id, "name": row["name"], "items": rendered_items, "filter": spec.get("filter")}

    def dashboard_list(self) -> dict:
        return {"dashboards": [{"id": r["id"], "name": r["name"], "updated_at": r["updated_at"]}
                                 for r in self.meta.list_dashboards()]}

    # ---- dashboards as code ----------------------------------------------------
    def dac_semantic_get(self) -> dict:
        doc = dac_semantic.load(self.config)
        path = self.config.data_dir / "semantic.yaml"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"doc": doc, "text": text}

    def dac_semantic_put(self, text: str, source: str = "ui") -> dict:
        def do():
            doc = dac_semantic.save(self.config, text)
            issues = dac_semantic.validate(doc, self.engine)
            n_err = sum(1 for i in issues if i["level"] == "error")
            return {"doc": doc, "issues": issues, "_log_summary": f"semantic layer saved ({n_err} error(s))"}

        return self._log("dac_semantic", source, None, {"chars": len(text or "")}, do)

    def dac_semantic_validate(self, text: Optional[str] = None) -> dict:
        doc = dac_semantic.parse(text) if text is not None else dac_semantic.load(self.config)
        return {"issues": dac_semantic.validate(doc, self.engine)}

    def dac_semantic_suggest(self, dataset_name: str, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)
        prof = self.profile(dataset_row["name"])
        doc = dac_semantic.suggest(dataset_row["name"], prof["columns"], prof["profile"], prof["row_count"])
        return self._log("dac_semantic_suggest", source, dataset_row["name"], {},
                          lambda: {"doc": doc, "_log_summary": f"suggested a semantic model for {dataset_row['name']!r}"})

    def dac_list(self) -> dict:
        return {"dashboards": dac_store.list_dashboards(self.config)}

    def dac_get(self, slug: str) -> dict:
        return dac_store.get(self.config, slug)

    def dac_put(self, text: str, slug: Optional[str] = None, source: str = "ui") -> dict:
        def do():
            result = dac_store.put(self.config, text, slug)
            issues = dac_spec.validate(result["doc"], dac_semantic.load(self.config))
            n_err = sum(1 for i in issues if i["level"] == "error")
            return {**result, "issues": issues, "_log_summary": f"dashboard {result['slug']!r} saved ({n_err} error(s))"}

        return self._log("dac_dashboard", source, None, {"slug": slug}, do)

    def dac_delete(self, slug: str, source: str = "ui") -> dict:
        def do():
            dac_store.delete(self.config, slug)
            return {"ok": True, "_log_summary": f"deleted code dashboard {slug!r}"}

        return self._log("dac_dashboard", source, None, {"slug": slug, "action": "delete"}, do)

    def dac_rename(self, slug: str, name: str, source: str = "ui") -> dict:
        return self._log("dac_dashboard", source, None, {"slug": slug, "action": "rename", "name": name},
                          lambda: {**dac_store.rename(self.config, slug, name), "_log_summary": f"renamed to {name!r}"})

    def dac_validate(self, slug: Optional[str] = None, text: Optional[str] = None) -> dict:
        doc = dac_spec.parse(text) if text is not None else dac_store.get(self.config, slug)["doc"]
        return {"issues": dac_spec.validate(doc, dac_semantic.load(self.config))}

    def dac_render(self, slug: str, filters: Optional[dict] = None) -> dict:
        doc = dac_store.get(self.config, slug)["doc"]
        return dac_render.render(self, doc, filters or {})

    def dac_history(self, slug: str) -> dict:
        return {"slug": slug, "history": dac_store.history(self.config, slug)}

    def dac_diff(self, slug: str, a: str, b: str) -> dict:
        return dac_store.diff(self.config, slug, a, b)

    def dac_export(self, slug: str, filters: Optional[dict] = None, source: str = "ui") -> dict:
        doc = dac_store.get(self.config, slug)["doc"]
        return self._log("dac_export", source, None, {"slug": slug},
                          lambda: {**dac_store.export_to_items(self, doc, filters or {})})

    def dac_import(self, dashboard_id: int, source: str = "ui") -> dict:
        return self._log("dac_import", source, None, {"dashboard_id": dashboard_id},
                          lambda: {**dac_store.import_from_items(self, dashboard_id), "_log_summary": "imported (best effort)"})

    # ---- models --------------------------------------------------------------
    WRITE_TO_CHOICES = ("new_dataset", "new_version", "none")

    def _model_key_columns(self, df, used: list[str]) -> list[str]:
        """Best-effort natural key column(s) for a side output dataset — a
        near-unique id-like column not already used as a model input, so a
        row in the output can still be matched back to the source. See
        `models.is_id_like`."""
        return [c for c in df.columns if c not in used and model_engine.is_id_like(df[c], len(df))]

    def _write_model_output(self, dataset_row: sqlite3.Row, v: sqlite3.Row, df, model_id: int, suffix: str,
                              op: str, key_cols: list[str], inputs: list[str], extra: dict[str, list],
                              write_to: str) -> dict:
        """Write a model's per-row output (predictions/cluster labels/anomaly
        flags). Default (`write_to="new_dataset"`) puts it in its own new
        dataset named `<source>__<suffix>_<model_id>`, holding the key/id
        columns plus the inputs plus the output — the source dataset is never
        touched. `write_to="new_version"` is the old behaviour (a new version
        of the source itself) kept as an explicit opt-in, and even then only
        ever *adds* the new column(s): it can't change an existing column's
        type, unlike the old round-trip-through-pandas implementation (a real
        bug: a DATE column came back as TIMESTAMP, a DECIMAL as DOUBLE).
        `write_to="none"` skips writing anything."""
        import pandas as pd

        if write_to not in self.WRITE_TO_CHOICES:
            raise ValueError(f"unknown write_to: {write_to!r}; choose from {self.WRITE_TO_CHOICES}")
        if write_to == "none":
            return {"write_to": "none"}
        if write_to == "new_version":
            if len(df) < v["row_count"]:
                raise model_engine.ModelError(
                    f"dataset has {v['row_count']} rows, more than fit in the {len(df)}-row training limit; "
                    "write_to='new_version' can't safely align columns back onto the source — "
                    "use the default write_to='new_dataset' instead"
                )
            extra_df = pd.DataFrame(extra)
            new_version = max(r["version"] for r in self.meta.list_versions(dataset_row["id"])) + 1
            new_table = f"ds_{dataset_row['id']}_v{new_version}"
            row_count, columns = self.engine.append_columns(v["table_name"], new_table, extra_df)
            self.meta.add_version(dataset_row["id"], new_version, v["version"], op,
                                    {"model_id": model_id}, f"({op})", new_table, row_count, columns)
            self.meta.set_current_version(dataset_row["id"], new_version)
            self._refresh_view(self.meta.get_dataset_by_id(dataset_row["id"]))
            return {"write_to": "new_version", "dataset": dataset_row["name"], "version": new_version}
        # default: a separate dataset, source untouched. The carried source
        # columns are selected straight from the source table in DuckDB (not
        # from the in-memory `df`), so they keep their exact type (a DATE
        # stays a DATE, a DECIMAL stays a DECIMAL) instead of being re-typed
        # by a round trip through pandas -- only the new columns in `extra`
        # (the model's own output) come from pandas.
        carried = [c for c in dict.fromkeys([*key_cols, *inputs]) if c in df.columns]
        extra_df = pd.DataFrame(extra)
        out_name = slugify_name(f"{dataset_row['name']}__{suffix}_{model_id}")
        out_id = self.meta.add_dataset(out_name, None)
        out_table = f"ds_{out_id}_v0"
        row_count, columns = self.engine.select_columns_with_extra(v["table_name"], out_table, carried, extra_df)
        self.meta.add_version(out_id, 0, None, op, {"model_id": model_id, "source_dataset": dataset_row["name"]},
                                f"({op})", out_table, row_count, columns)
        self.meta.set_current_version(out_id, 0)
        self.engine.set_view(out_name, out_table)
        return {"write_to": "new_dataset", "dataset": out_name}

    def model_train(self, dataset_name: str, target: str, features: Optional[list[str]] = None,
                     task: Optional[str] = None, algorithm: Optional[str] = None, test_size: float = 0.2,
                     seed: int = 42, write_to: str = "new_dataset", name: Optional[str] = None,
                     source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = model_engine.train_supervised(df, target, features, task, algorithm, test_size, seed)
            model_id = self.meta.add_model(name or f"{dataset_row['name']}_{result['algorithm']}", dataset_row["id"],
                                             v["version"], result["task"], target, result["features"],
                                             {"algorithm": result["algorithm"], "test_size": test_size},
                                             result["metrics"], seed, None)
            out = {"model_id": model_id, **{k: v2 for k, v2 in result.items() if k != "predictions"},
                    "_log_summary": f"trained {result['task']} ({result['algorithm']}) on {dataset_row['name']!r}"}
            pred_col = f"predicted_{target}"
            write_result = self._write_model_output(
                dataset_row, v, df, model_id, "model", "model_predict",
                key_cols=self._model_key_columns(df, [*result["features"], target]),
                inputs=[*result["features"], target],
                extra={pred_col: result["predictions"]}, write_to=write_to,
            )
            out.update(write_result)
            out["prediction_column"] = pred_col
            return out

        return self._log("model", source, dataset_row["name"], {"target": target, "algorithm": algorithm},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_cluster(self, dataset_name: str, features: list[str], k: Optional[int] = None, seed: int = 42,
                       write_to: str = "new_dataset", name: Optional[str] = None, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = model_engine.run_kmeans(df, features, k=k, seed=seed)
            model_id = self.meta.add_model(name or f"{dataset_row['name']}_kmeans", dataset_row["id"], v["version"],
                                             "clustering", None, features, {"k": result["k"]},
                                             {"elbow": result["elbow"], "silhouette": result["silhouette"]}, seed, None)
            out = {"model_id": model_id, **{k2: v2 for k2, v2 in result.items() if k2 != "labels"},
                    "_log_summary": f"kmeans k={result['k']} on {dataset_row['name']!r}"}
            write_result = self._write_model_output(
                dataset_row, v, df, model_id, "clusters", "model_cluster",
                key_cols=self._model_key_columns(df, features), inputs=features,
                extra={"cluster": result["labels"]}, write_to=write_to,
            )
            out.update(write_result)
            return out

        return self._log("cluster", source, dataset_row["name"], {"features": features, "k": k},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_pca(self, dataset_name: str, features: list[str], n_components: int = 2, seed: int = 42,
                   source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = model_engine.run_pca(df, features, n_components=n_components, seed=seed)
            result["_log_summary"] = f"PCA {n_components}D on {dataset_row['name']!r}"
            return result

        return self._log("model", source, dataset_row["name"], {"kind": "pca", "features": features},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_anomaly(self, dataset_name: str, features: list[str], contamination: float = 0.05, seed: int = 42,
                       write_to: str = "new_dataset", name: Optional[str] = None, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = model_engine.run_anomaly(df, features, contamination=contamination, seed=seed)
            model_id = self.meta.add_model(name or f"{dataset_row['name']}_isolation_forest", dataset_row["id"],
                                             v["version"], "anomaly", None, features, {"contamination": contamination},
                                             {"n_anomalies": result["n_anomalies"]}, seed, None)
            out = {"model_id": model_id, **{k: v2 for k, v2 in result.items() if k not in ("is_anomaly", "anomaly_score")},
                    "_log_summary": f"{result['n_anomalies']} anomalies found in {dataset_row['name']!r}"}
            write_result = self._write_model_output(
                dataset_row, v, df, model_id, "anomalies", "model_anomaly",
                key_cols=self._model_key_columns(df, features), inputs=features,
                extra={"is_anomaly": result["is_anomaly"], "anomaly_score": result["anomaly_score"]},
                write_to=write_to,
            )
            out.update(write_result)
            return out

        return self._log("model", source, dataset_row["name"], {"kind": "anomaly", "features": features},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_forecast(self, dataset_name: str, date_col: str, value_col: str, horizon: int = 12,
                        seasonal_period: Optional[int] = None, name: Optional[str] = None,
                        write_to: str = "new_dataset", source: str = "ui", freq: str = "auto") -> dict:
        dataset_row = self._dataset_row(dataset_name)
        if write_to not in ("new_dataset", "none"):
            raise ValueError(f"unknown write_to: {write_to!r}; forecast supports 'new_dataset' or 'none' "
                              "(its output has different rows than the source, so 'new_version' doesn't apply)")

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = model_engine.run_forecast(df, date_col, value_col, horizon=horizon,
                                                seasonal_period=seasonal_period, freq=freq)
            model_id = self.meta.add_model(name or f"{dataset_row['name']}_forecast", dataset_row["id"], v["version"],
                                             "forecast", value_col, [date_col],
                                             {"horizon": horizon, "method": result["method"], "freq": freq,
                                              "resampled_to": result["resampled_to"]},
                                             {"seasonal_period": result["seasonal_period"]}, 42, None)
            _freq_adverb = {"day": "daily", "week": "weekly", "month": "monthly", "quarter": "quarterly"}
            out = {"model_id": model_id, **result,
                    "_log_summary": f"forecast {horizon} steps ({result['method']}, "
                                     f"{_freq_adverb.get(result['resampled_to'], result['resampled_to'])}) "
                                     f"for {dataset_row['name']!r}"}
            if write_to == "new_dataset":
                import pandas as pd

                fc_df = pd.DataFrame(result["forecast"])
                fc_name = slugify_name(f"{dataset_row['name']}__forecast_{model_id}")
                fc_id = self.meta.add_dataset(fc_name, None)
                fc_table = f"ds_{fc_id}_v0"
                row_count, columns = self.engine.materialize_from_dataframe(fc_table, fc_df)
                self.meta.add_version(fc_id, 0, None, "forecast_output",
                                        {"model_id": model_id, "source_dataset": dataset_row["name"]},
                                        "(forecast)", fc_table, row_count, columns)
                self.meta.set_current_version(fc_id, 0)
                self.engine.set_view(fc_name, fc_table)
                out["write_to"] = "new_dataset"
                out["forecast_dataset"] = fc_name
            else:
                out["write_to"] = "none"
            return out

        return self._log("forecast", source, dataset_row["name"],
                          {"date_col": date_col, "value_col": value_col, "horizon": horizon},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_list(self, dataset_name: Optional[str] = None) -> dict:
        dataset_id = self._dataset_row(dataset_name)["id"] if dataset_name else None
        import json as _json

        out = []
        for m in self.meta.list_models(dataset_id):
            out.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "target": m["target"],
                         "features": _json.loads(m["features_json"]), "metrics": _json.loads(m["metrics_json"]),
                         "created_at": m["created_at"], "dataset_id": m["dataset_id"]})
        return {"models": out}

    # ---- export --------------------------------------------------------------
    def workbook_inventory(self, dataset_name: str, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            import json as _json

            if not dataset_row["source_id"]:
                raise DataError("dataset has no linked source workbook")
            source_row = self.meta.get_source(dataset_row["source_id"])
            if source_row is None:
                raise DataError("dataset's source workbook record no longer exists")
            source_options = _json.loads(source_row["options_json"] or "{}") if source_row else {}
            snapshot = source_options.get("workbook_snapshot")
            if not snapshot:
                raise DataError("dataset has no recorded workbook inventory; re-ingest its .xlsx source")
            source_path = Path(source_row["path"])
            current_sha = workbook_export.sha256_file(source_path) if source_path.exists() else None
            initial = self.meta.get_version(dataset_row["id"], 0)
            ingest = _json.loads(initial["params_json"] or "{}") if initial else {}
            return {"dataset": dataset_row["name"], "dataset_version": dataset_row["current_version"],
                    "source_id": dataset_row["source_id"], "source_path": str(source_path),
                    "source_sha256": snapshot["sha256"], "current_source_sha256": current_sha,
                    "source_matches_snapshot": current_sha == snapshot["sha256"],
                    "format": snapshot["format"], "bytes": snapshot["bytes"],
                    "package_part_count": snapshot["package_part_count"], "package_parts": snapshot["package_parts"],
                    "sheets": snapshot["sheets"], "selected_sheet": ingest.get("sheet"),
                    "selected_import_options": {"header_row": int(ingest.get("skip_rows", 0) or 0) + 1,
                                                "initial_data_rows": initial["row_count"] if initial else None,
                                                "columns": [c["name"] for c in _json.loads(initial["columns_json"] or "[]")] if initial else []}}

        return self._log("workbook_inventory", source, dataset_row["name"], {}, do)

    def export(self, dataset_name: str, fmt: str = "csv", path: Optional[str] = None, source: str = "ui",
               mode: str = "flat", version: Optional[int] = None) -> dict:
        if mode == "inventory":
            return self.workbook_inventory(dataset_name, source=source)
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row, version)
            fmt_l = fmt.lower()
            if fmt_l not in ("csv", "xlsx", "parquet", "json"):
                raise DataError(f"unsupported export format: {fmt}; choose csv/xlsx/parquet/json")
            if mode not in ("flat", "preserve_workbook"):
                raise DataError("export mode must be 'flat' or 'preserve_workbook'")
            if mode == "preserve_workbook" and fmt_l != "xlsx":
                raise DataError("preserve_workbook mode requires format='xlsx'")
            filename = path or (f"{dataset_row['name']}_v{v['version']}_workbook.xlsx" if mode == "preserve_workbook" else f"{dataset_row['name']}.{fmt_l}")
            out_path = (self.config.exports_dir / filename).resolve()
            if not hl_paths.is_inside(out_path, self.config.exports_dir):
                raise DataError("export path must stay inside the app's exports folder")
            if mode == "preserve_workbook" and out_path.suffix.lower() != ".xlsx":
                raise DataError("preserving workbook output path must end in .xlsx")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "preserve_workbook":
                import json as _json

                if not dataset_row["source_id"]:
                    raise DataError("dataset has no original workbook source")
                source_row = self.meta.get_source(dataset_row["source_id"])
                source_options = _json.loads(source_row["options_json"] or "{}") if source_row else {}
                snapshot = source_options.get("workbook_snapshot")
                if not snapshot:
                    raise DataError("dataset has no recorded workbook snapshot; re-ingest its .xlsx source")
                original = self.meta.get_version(dataset_row["id"], 0)
                original_params = _json.loads(original["params_json"] or "{}") if original else {}
                sheet_name = original_params.get("sheet")
                if not sheet_name:
                    raise DataError("dataset is not linked to an imported workbook sheet")
                original_columns = [column["name"] for column in _json.loads(original["columns_json"] or "[]")]
                current_columns = [column["name"] for column in _json.loads(v["columns_json"] or "[]")]
                if current_columns != original_columns:
                    raise DataError("preserving export requires the original columns in the original order; rename/drop/derive is unsupported")
                with self.engine.lock() as conn:
                    cur = conn.execute(f'SELECT * FROM "{v["table_name"]}"')
                    rows = cur.fetchall()
                    original_cur = conn.execute(f'SELECT * FROM "{original["table_name"]}"')
                    original_rows = original_cur.fetchall()
                skip_rows = int(original_params.get("skip_rows", 0) or 0)
                receipt = workbook_export.export_copy(
                    Path(source_row["path"]), out_path, snapshot, sheet_name=sheet_name,
                    header_row=skip_rows + 1, row_count=original["row_count"], columns=current_columns,
                    rows=rows, original_rows=original_rows, dataset=dataset_row["name"],
                    version=v["version"], source_id=dataset_row["source_id"])
                receipt_path = out_path.with_suffix(".workbook-export.json")
                receipt["receipt_path"] = str(receipt_path)
                return {**receipt, "_log_summary": f"exported {dataset_row['name']!r} v{v['version']} into a copy of {sheet_name!r} in {out_path.name}"}
            with self.engine.lock() as conn:
                if fmt_l == "csv":
                    conn.execute(f'COPY (SELECT * FROM "{v["table_name"]}") TO ? (FORMAT CSV, HEADER)', [str(out_path)])
                elif fmt_l == "parquet":
                    conn.execute(f'COPY (SELECT * FROM "{v["table_name"]}") TO ? (FORMAT PARQUET)', [str(out_path)])
                elif fmt_l == "json":
                    conn.execute(f'COPY (SELECT * FROM "{v["table_name"]}") TO ? (FORMAT JSON, ARRAY true)', [str(out_path)])
                else:  # xlsx: DuckDB has no native writer; build with openpyxl from the query result
                    self._write_xlsx(v["table_name"], out_path)
            return {"path": str(out_path), "format": fmt_l, "row_count": v["row_count"], "dataset": dataset_row["name"],
                     "dataset_version": v["version"], "mode": "flat",
                     "_log_summary": f"exported {dataset_row['name']!r} to {out_path.name}"}

        result = self._log("export", source, dataset_row["name"], {"format": fmt, "mode": mode, "version": version},
                           lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))
        if result.get("mode") == "preserve_workbook":
            import json as _json

            receipt_path = Path(result["receipt_path"])
            result["analysis_log_id"] = result["log_id"]
            receipt = {key: value for key, value in result.items() if key not in {"log_id", "receipt_sha256"}}
            receipt["analysis_log_id"] = result["log_id"]
            encoded = _json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
            temp = receipt_path.with_name(receipt_path.name + "." + secrets.token_hex(6) + ".tmp")
            temp.write_bytes(encoded)
            temp.replace(receipt_path)
            result["receipt_sha256"] = workbook_export.sha256_file(receipt_path)
        return result

    def _write_xlsx(self, table_name: str, out_path: Path) -> None:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "data"
        with self.engine.lock() as conn:
            cur = conn.execute(f'SELECT * FROM "{table_name}"')
            cols = [d[0] for d in cur.description]
            ws.append(cols)
            for row in cur.fetchall():
                ws.append([str(v) if isinstance(v, (dict, list)) else v for v in row])
        wb.save(out_path)

    # ---- analysis log --------------------------------------------------------
    def log_search(self, q: Optional[str] = None, source: Optional[str] = None, dataset: Optional[str] = None,
                    limit: int = 50) -> dict:
        rows = self.meta.search_log(q, source, dataset, limit)
        return {"entries": [dict(r) for r in rows]}

    def log_get(self, op_id: str) -> dict:
        row = self.meta.get_log(op_id)
        if row is None:
            raise NotFoundError(f"unknown log entry: {op_id}")
        return dict(row)

    # ---- ask your data ---------------------------------------------------------
    async def ask(self, question: str, datasets: Optional[list[str]] = None, source: str = "ui") -> dict:
        import json as _json

        names = datasets or [d["name"] for d in self.meta.list_datasets()]
        views = {}
        for name in names:
            dataset_row = self._dataset_row(name)
            v = self._version_row(dataset_row)
            views[dataset_row["name"]] = _json.loads(v["columns_json"])
        start = time.monotonic()
        try:
            result = await ask_engine.ask(self.engine, self.link, question, views)
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.monotonic() - start) * 1000
            self.meta.log("ask", source, None, {"question": question}, "", False, str(exc), elapsed)
            raise
        elapsed = (time.monotonic() - start) * 1000
        op_id = self.meta.log("ask", source, None, {"question": question}, f"{result['row_count']} row(s)", True, None, elapsed)
        result["log_id"] = op_id
        return result

    def ask_available(self) -> dict:
        return {"used_capabilities": list(backend.USED_CAPABILITIES),
                 "config_error": backend.config_error(self.config.data_dir)}

    # ---- lab: EDA / quality / drift ------------------------------------------
    def _dataset_df(self, dataset_name: str, version: Optional[int] = None):
        dataset_row = self._dataset_row(dataset_name)
        v = self._version_row(dataset_row, version)
        return self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF), dataset_row, v

    def lab_eda_profile(self, dataset_name: str, source: str = "ui") -> dict:
        def do():
            df, dataset_row, v = self._dataset_df(dataset_name)
            result = lab_eda.eda_profile(df)
            result["dataset"], result["version"] = dataset_row["name"], v["version"]
            result["_log_summary"] = f"EDA on {dataset_row['name']!r}: quality {result['quality_score']['score']}/100"
            return result

        return self._log("lab_eda", source, dataset_name, {}, lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_quality_score(self, dataset_name: str, source: str = "ui") -> dict:
        def do():
            df, dataset_row, v = self._dataset_df(dataset_name)
            result = lab_eda.quality_score(df)
            result["dataset"], result["version"] = dataset_row["name"], v["version"]
            result["_log_summary"] = f"quality score {result['score']}/100 for {dataset_row['name']!r}"
            return result

        return self._log("lab_quality_score", source, dataset_name, {}, lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_drift(self, dataset_name: str, other_dataset: str, columns: Optional[list[str]] = None,
                  source: str = "ui") -> dict:
        def do():
            df_a, dataset_row, _ = self._dataset_df(dataset_name)
            df_b, other_row, _ = self._dataset_df(other_dataset)
            result = lab_drift.compare_distributions(df_a, df_b, columns)
            result["dataset_a"], result["dataset_b"] = dataset_row["name"], other_row["name"]
            result["_log_summary"] = f"drift {dataset_row['name']!r} vs {other_row['name']!r}: {result['n_flagged']} flagged"
            return result

        return self._log("lab_drift", source, dataset_name, {"other_dataset": other_dataset, "columns": columns},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_compare_curves(self, dataset_name: str, x: str, y: str, group: Optional[str] = None,
                            source: str = "ui") -> dict:
        def do():
            df, dataset_row, _ = self._dataset_df(dataset_name)
            result = lab_drift.curve_comparison(df, x, y, group)
            result["dataset"] = dataset_row["name"]
            result["_log_summary"] = f"curve comparison on {dataset_row['name']!r} ({x} vs {y})"
            return result

        return self._log("lab_compare", source, dataset_name, {"x": x, "y": y, "group": group},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    # ---- lab: model registry --------------------------------------------------
    def lab_backends(self, task: Optional[str] = None) -> dict:
        return lab_registry.list_backends(task)

    def _lab_model_row(self, model_id: int) -> sqlite3.Row:
        row = self.meta.get_model(model_id)
        if row is None:
            raise NotFoundError(f"unknown model: {model_id}")
        return row

    def _lab_load(self, model_id: int) -> tuple[sqlite3.Row, "lab_registry.SavedModel"]:
        row = self._lab_model_row(model_id)
        if not row["artifact_path"]:
            raise LabError(f"model {model_id} has no saved artifact (trained before Lab, or write_to='none')")
        return row, lab_registry.load_model(row["artifact_path"])

    def _lab_training_df(self, row: sqlite3.Row):
        dataset_row = self.meta.get_dataset_by_id(row["dataset_id"])
        if dataset_row is None:
            raise NotFoundError(f"model {row['id']}'s training dataset no longer exists")
        v = self._version_row(dataset_row, row["dataset_version"])
        return self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF), dataset_row

    def _lab_register(self, dataset_row: sqlite3.Row, version: int, result: dict, name: Optional[str],
                       extra_params: dict) -> int:
        model_id = self.meta.add_model(
            name or f"{dataset_row['name']}_{result['backend']}", dataset_row["id"], version, result["task"],
            result["target"], result["features"],
            {"backend": result["backend"], "lab": True, **extra_params}, result["metrics"], extra_params.get("seed", 42),
            None,
        )
        path = lab_registry.save_model(self.config.lab_models_dir, model_id, result)
        self.meta.set_model_artifact_path(model_id, path)  # only known once model_id exists
        return model_id

    def lab_model_train(self, dataset_name: str, target: str, features: Optional[list[str]] = None,
                         task: Optional[str] = None, backend_name: str = "random_forest", test_size: float = 0.2,
                         seed: int = 42, write_to: str = "new_dataset", name: Optional[str] = None,
                         source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = lab_registry.train_model(df, target, features, task, backend_name, test_size, seed)
            model_id = self._lab_register(dataset_row, v["version"], result, name, {"test_size": test_size, "seed": seed})
            X_all, _ = lab_registry.prep_features(df, result["features"])
            X_all = X_all.reindex(columns=result["_X_columns"], fill_value=0)
            predictions = result["_model"].predict(X_all)
            if result["_label_encoder"] is not None:
                predictions = result["_label_encoder"].inverse_transform(predictions.astype(int))
            out = {"model_id": model_id,
                   **{k: v2 for k, v2 in result.items() if not k.startswith("_")},
                   "_log_summary": f"trained {result['task']} ({result['backend']}) on {dataset_row['name']!r}"}
            pred_col = f"predicted_{target}"
            write_result = self._write_model_output(
                dataset_row, v, df, model_id, "lab_model", "lab_model_train",
                key_cols=self._model_key_columns(df, [*result["features"], target]),
                inputs=[*result["features"], target],
                extra={pred_col: list(predictions)}, write_to=write_to,
            )
            out.update(write_result)
            out["prediction_column"] = pred_col
            return out

        return self._log("lab_model_train", source, dataset_row["name"], {"target": target, "backend": backend_name},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_model_tune(self, dataset_name: str, target: str, features: Optional[list[str]] = None,
                        task: Optional[str] = None, backend_name: str = "random_forest",
                        param_space: Optional[dict] = None, n_trials: int = 20, timeout: Optional[float] = None,
                        cv: int = 5, seed: int = 42, test_size: float = 0.2, name: Optional[str] = None,
                        source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            df = self.engine.to_dataframe(v["table_name"], limit=MAX_CHART_ROWS_FOR_DF)
            result = lab_tuning.tune_model(df, target, features, task, backend_name, param_space, n_trials,
                                            timeout, cv, seed, test_size)
            model_id = self._lab_register(dataset_row, v["version"], result, name,
                                           {"seed": seed, "tuned": True, "tuning_method": result["tuning"]["method"]})
            return {"model_id": model_id, **{k: v2 for k, v2 in result.items() if not k.startswith("_")},
                    "_log_summary": f"tuned {result['backend']} on {dataset_row['name']!r} "
                                     f"({result['tuning']['method']}, best cv={result['tuning']['best_cv_score']})"}

        return self._log("lab_model_tune", source, dataset_row["name"], {"target": target, "backend": backend_name},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_model_evaluate(self, model_id: int, eval_dataset: Optional[str] = None, date_col: Optional[str] = None,
                            group_col: Optional[str] = None, source: str = "ui") -> dict:
        def do():
            row, saved = self._lab_load(model_id)
            df_train, train_dataset_row = self._lab_training_df(row)
            note = None
            if eval_dataset:
                df_eval, eval_row, _ = self._dataset_df(eval_dataset)
                eval_name = eval_row["name"]
            else:
                df_eval, eval_name = df_train, train_dataset_row["name"]
                note = "no eval_dataset given; evaluated in-sample against the training dataset"
            result = lab_diagnostics.evaluate_model(saved, df_eval, df_train, seed=42, date_col=date_col,
                                                      group_col=group_col)
            result["model_id"] = model_id
            result["eval_dataset"] = eval_name
            if note:
                result["note"] = note
            result["_log_summary"] = f"evaluated model {model_id} on {eval_name!r}"
            return result

        return self._log("lab_model_evaluate", source, None, {"model_id": model_id, "eval_dataset": eval_dataset},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_model_explain(self, model_id: int, dataset_name: Optional[str] = None, sample_size: int = 200,
                           row_index: Optional[int] = None, seed: int = 42, source: str = "ui") -> dict:
        def do():
            row, saved = self._lab_load(model_id)
            if dataset_name:
                df, dataset_row, _ = self._dataset_df(dataset_name)
            else:
                df, dataset_row = self._lab_training_df(row)
            result = lab_explain.explain_model(saved, df, sample_size, row_index, seed)
            result["model_id"] = model_id
            result["dataset"] = dataset_row["name"]
            result["_log_summary"] = f"explained model {model_id} ({result['method']}) on {dataset_row['name']!r}"
            return result

        return self._log("lab_model_explain", source, None, {"model_id": model_id, "dataset": dataset_name},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_model_optimize(self, model_id: int, direction: str = "maximize", bounds: Optional[dict] = None,
                            fixed: Optional[dict] = None, integer_features: Optional[list[str]] = None,
                            categorical_features: Optional[dict] = None, constraints: Optional[list[dict]] = None,
                            acquisition: str = "ei", n_candidates: int = 3000, batch_size: int = 5, seed: int = 42,
                            write_to: str = "none", source: str = "ui") -> dict:
        def do():
            row, saved = self._lab_load(model_id)
            df, dataset_row = self._lab_training_df(row)
            result = lab_optimize.optimize(saved, df, direction, bounds, fixed, integer_features,
                                            categorical_features, constraints, acquisition, n_candidates,
                                            batch_size, seed)
            result["model_id"] = model_id
            result["_log_summary"] = f"optimize model {model_id}: {len(result['suggested_points'])} suggestion(s)"
            if write_to == "new_dataset":
                import pandas as pd

                rows = [{**s["inputs"], "predicted_value": s["predicted_value"], "uncertainty": s["uncertainty"]}
                        for s in result["suggested_points"]]
                out_df = pd.DataFrame(rows)
                out_name = slugify_name(f"{dataset_row['name']}__optimize_{model_id}")
                out_id = self.meta.add_dataset(out_name, None)
                out_table = f"ds_{out_id}_v0"
                row_count, columns = self.engine.materialize_from_dataframe(out_table, out_df)
                self.meta.add_version(out_id, 0, None, "lab_optimize_output", {"model_id": model_id}, "(optimize)",
                                        out_table, row_count, columns)
                self.meta.set_current_version(out_id, 0)
                self.engine.set_view(out_name, out_table)
                result["write_to"] = "new_dataset"
                result["optimize_dataset"] = out_name
            else:
                result["write_to"] = "none"
            return result

        return self._log("lab_model_optimize", source, None, {"model_id": model_id, "direction": direction},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_pareto(self, model_ids: list[int], directions: list[str], bounds: Optional[dict] = None,
                    fixed: Optional[dict] = None, n_candidates: int = 1000, seed: int = 42,
                    write_to: str = "none", source: str = "ui") -> dict:
        def do():
            saved_models = []
            dfs = []
            for mid in model_ids:
                row, saved = self._lab_load(mid)
                df, _ = self._lab_training_df(row)
                saved_models.append(saved)
                dfs.append(df)
            import pandas as pd

            merged = pd.concat(dfs, ignore_index=True, sort=False) if len(dfs) > 1 else dfs[0]
            result = lab_optimize.pareto_front(saved_models, merged, directions, bounds, fixed, n_candidates, seed)
            result["model_ids"] = model_ids
            result["_log_summary"] = f"pareto front over {len(model_ids)} model(s): {result['n_front']} point(s)"
            if write_to == "new_dataset":
                rows = [{**p["inputs"], **{f"objective_{i}": v for i, v in enumerate(p["objectives"])}}
                        for p in result["front"]]
                out_df = pd.DataFrame(rows)
                out_name = slugify_name(f"pareto_{'_'.join(str(m) for m in model_ids)}")
                out_id = self.meta.add_dataset(out_name, None)
                out_table = f"ds_{out_id}_v0"
                row_count, columns = self.engine.materialize_from_dataframe(out_table, out_df)
                self.meta.add_version(out_id, 0, None, "lab_pareto_output", {"model_ids": model_ids}, "(pareto)",
                                        out_table, row_count, columns)
                self.meta.set_current_version(out_id, 0)
                self.engine.set_view(out_name, out_table)
                result["write_to"] = "new_dataset"
                result["pareto_dataset"] = out_name
            else:
                result["write_to"] = "none"
            return result

        return self._log("lab_pareto", source, None, {"model_ids": model_ids, "directions": directions},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_registry_list(self, dataset_name: Optional[str] = None) -> dict:
        import json as _json

        dataset_id = self._dataset_row(dataset_name)["id"] if dataset_name else None
        out = []
        for m in self.meta.list_models(dataset_id):
            params = _json.loads(m["params_json"])
            if not params.get("lab"):
                continue
            out.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "target": m["target"],
                         "backend": params.get("backend"), "features": _json.loads(m["features_json"]),
                         "params": params, "metrics": _json.loads(m["metrics_json"]),
                         "created_at": m["created_at"], "dataset_id": m["dataset_id"],
                         "dataset_version": m["dataset_version"], "has_artifact": bool(m["artifact_path"])})
        return {"models": out}

    def lab_registry_get(self, model_id: int) -> dict:
        import json as _json

        row = self._lab_model_row(model_id)
        params = _json.loads(row["params_json"])
        return {"id": row["id"], "name": row["name"], "kind": row["kind"], "target": row["target"],
                "backend": params.get("backend"), "features": _json.loads(row["features_json"]), "params": params,
                "metrics": _json.loads(row["metrics_json"]), "dataset_id": row["dataset_id"],
                "dataset_version": row["dataset_version"], "created_at": row["created_at"],
                "has_artifact": bool(row["artifact_path"])}

    def lab_registry_compare(self, model_ids: list[int]) -> dict:
        return {"models": [self.lab_registry_get(mid) for mid in model_ids]}

    def lab_registry_delete(self, model_id: int, source: str = "ui") -> dict:
        row = self._lab_model_row(model_id)
        path = row["artifact_path"]
        self.meta.delete_model(model_id)
        if path:
            Path(path).unlink(missing_ok=True)
        return {"ok": True, "model_id": model_id}

    def lab_report(self, dataset_name: str, model_id: Optional[int] = None, eval_dataset: Optional[str] = None,
                    optimize: bool = False, optimize_params: Optional[dict] = None, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            eda_result = self.lab_eda_profile(dataset_row["name"], source="agent")
            model_result = None
            diagnostics_result = None
            optimize_result = None
            if model_id:
                model_result = self.lab_registry_get(model_id)
                diagnostics_result = self.lab_model_evaluate(model_id, eval_dataset, source="agent")
                if optimize:
                    optimize_result = self.lab_model_optimize(model_id, source="agent", **(optimize_params or {}))
            out_path = self.config.exports_dir / f"lab_report_{dataset_row['name']}_{int(self.now())}.pdf"
            path = lab_report_mod.build_report(out_path, dataset_row["name"], eda_result, model_result,
                                                 diagnostics_result, optimize_result)
            return {"path": str(path), "_log_summary": f"Lab report for {dataset_row['name']!r} -> {path.name}"}

        return self._log("lab_report", source, dataset_row["name"], {"model_id": model_id},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

    def lab_report_path(self, path: str) -> Path:
        """Resolve a Lab report path (as returned by `lab_report`) to a real
        file inside the exports directory — never anything else on disk."""
        exports_dir = self.config.exports_dir.resolve()
        candidate = Path(path).resolve()
        if exports_dir not in candidate.parents or not candidate.is_file():
            raise LookupError(f"report file not found: {path!r}")
        return candidate

    # ---- lab: visual pipeline --------------------------------------------------
    def lab_pipeline_graph(self, dataset_name: str) -> dict:
        dataset_row = self._dataset_row(dataset_name)
        steps = self.recipe(dataset_row["name"], "show")["steps"]
        graph = lab_pipeline.dataset_graph(dataset_row["current_version"], steps)
        graph["dataset"] = dataset_row["name"]
        return graph

    def lab_pipeline_apply(self, dataset_name: str, graph: dict, dry_run: bool = False, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            import json as _json

            steps = lab_pipeline.validate_graph(graph)
            if dry_run:
                # a preview never mutates version history, so it never needs the per-dataset write lock
                v0 = self._version_row(dataset_row, 0)
                current_table = v0["table_name"]
                temp_tables = []
                row_count, columns = v0["row_count"], _json.loads(v0["columns_json"])
                try:
                    for i, step in enumerate(steps):
                        new_table = f"__pipeline_preview_{dataset_row['id']}_{i}_{int(self.now() * 1000)}"
                        _, row_count, columns = self.engine.apply_step(current_table, new_table, step["op"],
                                                                         step["params"], self._resolve_table_by_name)
                        temp_tables.append(new_table)
                        current_table = new_table
                    preview_rows = self.engine.query(f'SELECT * FROM "{current_table}"', limit=20)["rows"]
                    return {"dry_run": True, "steps": steps, "row_count": row_count, "columns": columns,
                            "preview_rows": preview_rows,
                            "_log_summary": f"pipeline preview for {dataset_row['name']!r}: {len(steps)} step(s)"}
                finally:
                    for t in temp_tables:
                        self.engine.drop_table(t)
            # apply for real: branch off v0, discarding every recorded step after it (same rule as transform_apply)
            with self._dataset_lock(dataset_row["id"]):
                fresh = self.meta.get_dataset_by_id(dataset_row["id"])
                if fresh is None:
                    raise NotFoundError(f"dataset {dataset_row['name']!r} no longer exists")
                v0 = self._version_row(fresh, 0)
                dropped = self.meta.delete_versions_after(fresh["id"], 0)
                for d in dropped:
                    self.engine.drop_table(d["table_name"])
                current_table = v0["table_name"]
                current_version = 0
                for step in steps:
                    new_version = current_version + 1
                    new_table = f"ds_{fresh['id']}_v{new_version}"
                    select_sql, row_count, columns = self.engine.apply_step(current_table, new_table, step["op"],
                                                                              step["params"], self._resolve_table_by_name)
                    self.meta.add_version(fresh["id"], new_version, current_version, step["op"], step["params"],
                                            select_sql, new_table, row_count, columns)
                    self.meta.set_current_version(fresh["id"], new_version)
                    current_table, current_version = new_table, new_version
                row = self.meta.get_dataset_by_id(fresh["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"applied pipeline graph to {dataset_row['name']!r}: {len(steps)} step(s) -> v{current_version}"
            return summary

        return self._log("lab_pipeline_apply", source, dataset_row["name"], {"dry_run": dry_run, "n_nodes": len(graph.get("nodes", []))},
                          lambda: self.run_heavy(do, timeout=MAX_LAB_TIMEOUT_S))

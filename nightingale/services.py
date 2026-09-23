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
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__, backend, db
from .config import Config
from .workbench import ask as ask_engine
from .workbench.charts import ChartSpec, render_png, run_chart, to_vega_lite
from .workbench.engine import DataError, Engine, slugify_name
from .workbench import models as model_engine
from .workbench.quality import QualityError, RULE_KINDS, build_check
from .workbench.steps import StepError

log = logging.getLogger("nightingale")

MAX_INGEST_TIMEOUT_S = 300
MAX_MODEL_TIMEOUT_S = 180
MAX_CHART_ROWS_FOR_DF = 1_000_000


class NotFoundError(LookupError):
    pass


def write_token(config: Config) -> str:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    config.token_path.write_text(token, encoding="utf-8")
    try:
        config.token_path.chmod(0o600)
    except OSError:
        pass
    return token


class Services:
    def __init__(self, config: Config, clock: Optional[Callable[[], float]] = None):
        self.config = config
        self.clock = clock or time.time
        self.started_at = self.clock()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        self.meta = db.Meta(db.connect(config.data_dir))
        self.engine = Engine(config.duckdb_path)
        self.link = backend.load_link(config.data_dir)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="nightingale-work")
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

    # ---- ingestion ----------------------------------------------------------
    def _finish_ingest(self, name: str, source_id: Optional[int], select_sql: str, row_count: int,
                        columns: list[dict], op: str, options: dict, extra: dict,
                        probe_table: Optional[str] = None) -> dict:
        dataset_row = self.meta.get_dataset(name)
        if dataset_row is None:
            dataset_id = self.meta.add_dataset(name, source_id)
        else:
            dataset_id = dataset_row["id"]
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
        p = Path(str(path).strip().strip('"')).expanduser()
        if not p.is_absolute():
            p = (self.config.data_dir / p).resolve()
        if not p.exists():
            raise DataError(f"path does not exist: {p}")
        suffix = p.suffix.lower()

        def do():
            dataset_name = slugify_name(name or p.stem)
            source_id = self.meta.add_source(dataset_name, suffix.lstrip("."), str(p), options)
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
                out = []
                for sheet_name, result in sheets:
                    dname = slugify_name(f"{dataset_name}__{sheet_name}") if len(sheets) > 1 else dataset_name
                    out.append(self._finish_ingest(dname, source_id, result.select_sql, result.row_count,
                                                     result.columns, "ingest", {**options, "sheet": sheet_name}, result.extra,
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
        p = Path(str(path).strip()).expanduser()
        if not p.is_absolute():
            p = (self.config.data_dir / p).resolve()
        if not p.is_dir():
            raise DataError(f"not a folder: {p}")

        def do():
            dataset_name = slugify_name(name or p.name)
            source_id = self.meta.add_source(dataset_name, "folder", str(p), {"glob": glob})
            result = self.engine.ingest_folder(p, glob)
            return self._finish_ingest(dataset_name, source_id, result.select_sql, result.row_count,
                                        result.columns, "ingest", {"glob": glob}, result.extra,
                                        probe_table=result.table_name)

        return self._log("ingest", source, name, {"path": str(p), "glob": glob},
                          lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

    def ingest_url(self, url: str, name: Optional[str] = None, fmt: str = "csv", options: Optional[dict] = None,
                    source: str = "ui") -> dict:
        options = dict(options or {})
        if not url.startswith(("http://", "https://")):
            raise DataError("only http(s) URLs are supported")

        def do():
            dataset_name = slugify_name(name or url.rsplit("/", 1)[-1].split("?")[0] or "url_dataset")
            suffix = ".json" if fmt == "json" else ".csv"
            tmp_path = self.engine.cache_dir / f"__url_{secrets.token_hex(6)}{suffix}"
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - explicit http(s)-only check above
                tmp_path.write_bytes(resp.read())
            try:
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

    def refresh(self, dataset_name: str, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)
        source_row = self.meta.get_source(dataset_row["source_id"]) if dataset_row["source_id"] else None
        if source_row is None:
            raise DataError(f"dataset {dataset_name!r} has no re-ingestable source")

        def do():
            import json as _json

            options = _json.loads(source_row["options_json"] or "{}")
            kind, path = source_row["kind"], source_row["path"]
            p = Path(path)
            # capture the recorded recipe (every step up to the current tip) before wiping history
            old_steps = [{"op": v["op"], "params": _json.loads(v["params_json"])}
                          for v in self.meta.list_versions(dataset_row["id"])
                          if v["op"] != "ingest" and v["version"] <= dataset_row["current_version"]]
            if kind in ("csv", "tsv", "txt"):
                result = self.engine.ingest_delimited(p, options)
            elif kind == "parquet":
                result = self.engine.ingest_parquet(p)
            elif kind in ("json", "ndjson", "jsonl"):
                result = self.engine.ingest_json(p, options)
            elif kind.startswith("url_"):
                fmt = "json" if kind.endswith("json") else "csv"
                tmp_path = self.engine.cache_dir / f"__refresh_{secrets.token_hex(6)}.{fmt}"
                with urllib.request.urlopen(path, timeout=30) as resp:  # noqa: S310
                    tmp_path.write_bytes(resp.read())
                try:
                    result = self.engine.ingest_json(tmp_path, options) if fmt == "json" \
                        else self.engine.ingest_delimited(tmp_path, options)
                finally:
                    tmp_path.unlink(missing_ok=True)
            else:
                raise DataError(f"source kind {kind!r} cannot be refreshed directly; re-ingest it manually")

            # wipe the old version history (dropping its tables) and rebuild from the fresh raw data
            old_versions = self.meta.delete_all_versions(dataset_row["id"])
            for v in old_versions:
                self.engine.drop_table(v["table_name"])
            new_table = f"ds_{dataset_row['id']}_v0"
            row_count, columns = result.row_count, result.columns
            self.engine.rename_table(result.table_name, new_table)
            self.meta.add_version(dataset_row["id"], 0, None, "ingest", options, result.select_sql,
                                   new_table, row_count, columns)
            self.meta.set_current_version(dataset_row["id"], 0)
            self.meta.touch_source(source_row["id"])
            # replay the recorded recipe (every step after the original ingest) on the fresh raw data
            replayed = 0
            current_version = 0
            for step in old_steps:
                new_version = current_version + 1
                prev_table = self.meta.get_version(dataset_row["id"], current_version)["table_name"]
                new_table_v = f"ds_{dataset_row['id']}_v{new_version}"
                try:
                    select_sql, rc, cols = self.engine.apply_step(prev_table, new_table_v, step["op"], step["params"],
                                                                    self._resolve_table_by_name)
                except (DataError, StepError):
                    break  # a step no longer applies to the refreshed shape: stop replay here, keep what worked
                self.meta.add_version(dataset_row["id"], new_version, current_version, step["op"],
                                        step["params"], select_sql, new_table_v, rc, cols)
                self.meta.set_current_version(dataset_row["id"], new_version)
                current_version = new_version
                replayed += 1
            row = self.meta.get_dataset_by_id(dataset_row["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"refreshed {dataset_row['name']!r}: {row_count} raw rows, replayed {replayed} step(s)"
            summary["steps_replayed"] = replayed
            return summary

        return self._log("refresh", source, dataset_row["name"], {}, lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

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
            v = self._version_row(dataset_row)
            new_version = max(r["version"] for r in self.meta.list_versions(dataset_row["id"])) + 1
            new_table = f"ds_{dataset_row['id']}_v{new_version}"
            # a new step branching off a version that is not the tip discards the redo stack it replaces
            dropped = self.meta.delete_versions_after(dataset_row["id"], v["version"])
            for d in dropped:
                self.engine.drop_table(d["table_name"])
            select_sql, row_count, columns = self.engine.apply_step(
                v["table_name"], new_table, op, params, self._resolve_table_by_name
            )
            self.meta.add_version(dataset_row["id"], new_version, v["version"], op, params, select_sql,
                                    new_table, row_count, columns)
            self.meta.set_current_version(dataset_row["id"], new_version)
            row = self.meta.get_dataset_by_id(dataset_row["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"{op} on {dataset_row['name']!r} -> v{new_version} ({row_count} rows)"
            return summary

        return self._log("transform", source, dataset_row["name"], {"op": op, "params": params}, do)

    def undo(self, dataset_name: str, steps: int = 1, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            target = max(0, dataset_row["current_version"] - steps)
            if self.meta.get_version(dataset_row["id"], target) is None:
                raise DataError(f"no version {target} to undo to")
            self.meta.set_current_version(dataset_row["id"], target)
            row = self.meta.get_dataset_by_id(dataset_row["id"])
            self._refresh_view(row)
            summary = self.dataset_summary(row)
            summary["_log_summary"] = f"undo {dataset_row['name']!r} to v{target}"
            return summary

        return self._log("undo", source, dataset_row["name"], {"steps": steps}, do)

    def redo(self, dataset_name: str, steps: int = 1, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            versions = [v["version"] for v in self.meta.list_versions(dataset_row["id"])]
            target = dataset_row["current_version"] + steps
            if target not in versions:
                raise DataError(f"no version {target} to redo to (latest is v{max(versions)})")
            self.meta.set_current_version(dataset_row["id"], target)
            row = self.meta.get_dataset_by_id(dataset_row["id"])
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
                self.meta.add_result(rule["id"], dataset_row["id"], v["version"], passed, checked, failed, sample, message)
                results.append({"rule_id": rule["id"], "name": rule["name"], "kind": rule["kind"], "passed": passed,
                                  "checked": checked, "failed": failed, "sample": sample, "message": message})
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

    # ---- models --------------------------------------------------------------
    def model_train(self, dataset_name: str, target: str, features: Optional[list[str]] = None,
                     task: Optional[str] = None, algorithm: Optional[str] = None, test_size: float = 0.2,
                     seed: int = 42, write_predictions: bool = True, name: Optional[str] = None,
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
            if write_predictions:
                pred_col = f"predicted_{target}"
                df_out = df.copy()
                df_out[pred_col] = result["predictions"]
                new_version = max(r["version"] for r in self.meta.list_versions(dataset_row["id"])) + 1
                new_table = f"ds_{dataset_row['id']}_v{new_version}"
                row_count, columns = self.engine.materialize_from_dataframe(new_table, df_out)
                self.meta.add_version(dataset_row["id"], new_version, v["version"], "model_predict",
                                        {"model_id": model_id, "target": target}, "(model predictions)",
                                        new_table, row_count, columns)
                self.meta.set_current_version(dataset_row["id"], new_version)
                self._refresh_view(self.meta.get_dataset_by_id(dataset_row["id"]))
                out["prediction_dataset_version"] = new_version
                out["prediction_column"] = pred_col
            return out

        return self._log("model", source, dataset_row["name"], {"target": target, "algorithm": algorithm},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_cluster(self, dataset_name: str, features: list[str], k: Optional[int] = None, seed: int = 42,
                       write_labels: bool = True, name: Optional[str] = None, source: str = "ui") -> dict:
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
            if write_labels:
                df_out = df.copy()
                df_out["cluster"] = result["labels"]
                new_version = max(r["version"] for r in self.meta.list_versions(dataset_row["id"])) + 1
                new_table = f"ds_{dataset_row['id']}_v{new_version}"
                row_count, columns = self.engine.materialize_from_dataframe(new_table, df_out)
                self.meta.add_version(dataset_row["id"], new_version, v["version"], "model_cluster",
                                        {"model_id": model_id}, "(cluster labels)", new_table, row_count, columns)
                self.meta.set_current_version(dataset_row["id"], new_version)
                self._refresh_view(self.meta.get_dataset_by_id(dataset_row["id"]))
                out["prediction_dataset_version"] = new_version
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
                       write_flags: bool = True, name: Optional[str] = None, source: str = "ui") -> dict:
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
            if write_flags:
                df_out = df.copy()
                df_out["is_anomaly"] = result["is_anomaly"]
                df_out["anomaly_score"] = result["anomaly_score"]
                new_version = max(r["version"] for r in self.meta.list_versions(dataset_row["id"])) + 1
                new_table = f"ds_{dataset_row['id']}_v{new_version}"
                row_count, columns = self.engine.materialize_from_dataframe(new_table, df_out)
                self.meta.add_version(dataset_row["id"], new_version, v["version"], "model_anomaly",
                                        {"model_id": model_id}, "(anomaly flags)", new_table, row_count, columns)
                self.meta.set_current_version(dataset_row["id"], new_version)
                self._refresh_view(self.meta.get_dataset_by_id(dataset_row["id"]))
                out["prediction_dataset_version"] = new_version
            return out

        return self._log("model", source, dataset_row["name"], {"kind": "anomaly", "features": features},
                          lambda: self.run_heavy(do, timeout=MAX_MODEL_TIMEOUT_S))

    def model_forecast(self, dataset_name: str, date_col: str, value_col: str, horizon: int = 12,
                        seasonal_period: Optional[int] = None, name: Optional[str] = None,
                        write_dataset: bool = True, source: str = "ui", freq: str = "auto") -> dict:
        dataset_row = self._dataset_row(dataset_name)

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
            if write_dataset:
                import pandas as pd

                fc_df = pd.DataFrame(result["forecast"])
                fc_name = slugify_name(f"{dataset_row['name']}_forecast")
                fc_dataset = self.meta.get_dataset(fc_name)
                fc_id = fc_dataset["id"] if fc_dataset else self.meta.add_dataset(fc_name, None)
                new_version = 0 if not fc_dataset else max(r["version"] for r in self.meta.list_versions(fc_id)) + 1
                fc_table = f"ds_{fc_id}_v{new_version}"
                row_count, columns = self.engine.materialize_from_dataframe(fc_table, fc_df)
                self.meta.add_version(fc_id, new_version, None, "forecast_output", {"model_id": model_id}, "(forecast)",
                                        fc_table, row_count, columns)
                self.meta.set_current_version(fc_id, new_version)
                self.engine.set_view(fc_name, fc_table)
                out["forecast_dataset"] = fc_name
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
    def export(self, dataset_name: str, fmt: str = "csv", path: Optional[str] = None, source: str = "ui") -> dict:
        dataset_row = self._dataset_row(dataset_name)

        def do():
            v = self._version_row(dataset_row)
            fmt_l = fmt.lower()
            if fmt_l not in ("csv", "xlsx", "parquet", "json"):
                raise DataError(f"unsupported export format: {fmt}; choose csv/xlsx/parquet/json")
            filename = path or f"{dataset_row['name']}.{fmt_l}"
            out_path = (self.config.exports_dir / filename).resolve()
            if self.config.exports_dir.resolve() not in out_path.parents and out_path != self.config.exports_dir:
                raise DataError("export path must stay inside the app's exports folder")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with self.engine.lock() as conn:
                if fmt_l == "csv":
                    conn.execute(f'COPY (SELECT * FROM "{v["table_name"]}") TO ? (FORMAT CSV, HEADER)', [str(out_path)])
                elif fmt_l == "parquet":
                    conn.execute(f'COPY (SELECT * FROM "{v["table_name"]}") TO ? (FORMAT PARQUET)', [str(out_path)])
                elif fmt_l == "json":
                    conn.execute(f'COPY (SELECT * FROM "{v["table_name"]}") TO ? (FORMAT JSON, ARRAY true)', [str(out_path)])
                else:  # xlsx: DuckDB has no native writer; build with openpyxl from the query result
                    self._write_xlsx(v["table_name"], out_path)
            return {"path": str(out_path), "format": fmt_l, "row_count": v["row_count"],
                     "_log_summary": f"exported {dataset_row['name']!r} to {out_path.name}"}

        return self._log("export", source, dataset_row["name"], {"format": fmt}, lambda: self.run_heavy(do, timeout=MAX_INGEST_TIMEOUT_S))

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

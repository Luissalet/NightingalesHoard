"""Read what a slicer or a toolpath file already knows, and what Vulcan's inspection says about a model.

* ``parse_gcode``: a slicer G-code (PrusaSlicer, OrcaSlicer, Bambu Studio, Cura or anything that writes the usual
  ``;TYPE:`` comments). Time, filament and layers are read from the slicer's own comments when they are there, and
  the moves are walked as a cross-check (filament extruded by feature, path length, travel, height). Retractions are
  paid back before new filament counts, so they do not inflate the volume.
* ``parse_trajectory``: a plain list of points (CSV ``x,y[,z]``, JSON ``[[x,y,z],...]`` / ``{"points": [...]}``)
  for the total path of a concrete printer or a cutter.
* ``measurements_for``: the quote parameters that these readings can fill, with their provenance.
* ``inspection_warnings``: Vulcan's ``asset_inspect`` report as structured warnings (severity, code, which quote
  parameters they put in doubt, the suggested fix), plus ``fetch_inspection`` to ask Vulcan through the Hub.

Nothing is invented: a figure the file does not contain stays missing.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable, Optional

from .fabquote import FabQuoteError
from .texts import get_lang
from .texts import t as tx

MAX_BYTES = 60_000_000
MAX_POINTS = 2_000_000
_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])", re.I)
_WORD = re.compile(r"([XYZEFS])\s*(-?\d*\.?\d+)", re.I)
_KV = re.compile(r"^;\s*([^=:;]{2,80}?)\s*[=:]\s*(.+?)\s*$")
_SECS = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}


class ImportError_(FabQuoteError):
    pass


def _duration_s(text: str) -> Optional[float]:
    parts = _DUR.findall(text or "")
    if not parts:
        return None
    return sum(float(v) * _SECS[u.lower()] for v, u in parts)


def _first_float(text: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text or "")
    return float(m.group(0)) if m else None


def _feature_group(name: str) -> str:
    n = name.lower()
    if "support" in n:
        return "support"
    if any(w in n for w in ("skirt", "brim", "wipe", "prime", "custom", "purge", "draft shield")):
        return "excluded"
    if any(w in n for w in ("top", "bottom", "solid", "skin", "ironing", "roof", "floor")):
        return "skin"
    return "part"


def parse_gcode(text: str) -> dict:
    """Summary of a slicer G-code. Raises ImportError_ when it holds no extruding moves at all."""
    if not isinstance(text, str) or not text.strip():
        raise ImportError_(tx("fq.gc_empty"))
    if len(text) > MAX_BYTES:
        raise ImportError_(tx("fq.gc_too_big", max=MAX_BYTES // 1_000_000))
    warnings: list[str] = []
    head = text[:4000].lower()
    slicer = next((n for k, n in (("prusaslicer", "PrusaSlicer"), ("orcaslicer", "OrcaSlicer"), ("bambustudio", "Bambu Studio"),
                                  ("cura", "Cura"), ("superslicer", "SuperSlicer"), ("slic3r", "Slic3r"),
                                  ("simplify3d", "Simplify3D")) if k in head.replace(" ", "")), None)
    kv: dict[str, str] = {}
    pos = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}
    rel_xyz, rel_e = False, False
    feed = 1500.0
    retracted = 0.0
    feature = "(none)"
    seen_type = False
    feat_len: dict[str, float] = {}
    path_len = travel_len = 0.0
    time_s = 0.0
    z_levels: set[float] = set()
    max_z = 0.0
    xmin = ymin = math.inf
    xmax = ymax = -math.inf
    arcs = 0
    layer_marks = 0
    moves = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            if line.startswith(";TYPE:"):
                feature, seen_type = line[6:].strip() or "(none)", True
            elif line.startswith((";LAYER_CHANGE", ";LAYER:")):
                layer_marks += 1
            m = _KV.match(line)
            if m:
                kv.setdefault(m.group(1).strip().lower(), m.group(2).strip())
            continue
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        cmd = code.split(None, 1)[0].upper()
        if cmd in ("G0", "G1", "G2", "G3"):
            moves += 1
            if cmd in ("G2", "G3"):
                arcs += 1
            w = {k.upper(): float(v) for k, v in _WORD.findall(code)}
            if "F" in w and w["F"] > 0:
                feed = w["F"]
            nx = pos["x"] + w["X"] if rel_xyz and "X" in w else w.get("X", pos["x"])
            ny = pos["y"] + w["Y"] if rel_xyz and "Y" in w else w.get("Y", pos["y"])
            nz = pos["z"] + w["Z"] if rel_xyz and "Z" in w else w.get("Z", pos["z"])
            net = 0.0
            if "E" in w:
                de = w["E"] if rel_e else w["E"] - pos["e"]
                pos["e"] = pos["e"] + w["E"] if rel_e else w["E"]
                if de < 0:
                    retracted += -de
                elif de > 0:
                    pay = min(retracted, de)
                    retracted -= pay
                    net = de - pay
            dist = math.sqrt((nx - pos["x"]) ** 2 + (ny - pos["y"]) ** 2 + (nz - pos["z"]) ** 2)
            if dist > 0:
                time_s += dist / feed * 60.0
                if net > 1e-9:
                    path_len += dist
                    xmin, xmax, ymin, ymax = min(xmin, nx), max(xmax, nx), min(ymin, ny), max(ymax, ny)
                else:
                    travel_len += dist
            if net > 1e-9:
                feat_len[feature] = feat_len.get(feature, 0.0) + net
                z_levels.add(round(nz, 3))
                max_z = max(max_z, nz)
            pos["x"], pos["y"], pos["z"] = nx, ny, nz
        elif cmd == "G90":
            rel_xyz = False  # G90 leaves the extruder mode alone in most firmwares: M82 / M83 set it
        elif cmd == "G91":
            rel_xyz = True
        elif cmd == "M82":
            rel_e = False
        elif cmd == "M83":
            rel_e = True
        elif cmd == "G92":
            w = {k.upper(): float(v) for k, v in _WORD.findall(code)}
            for k in ("x", "y", "z", "e"):
                if k.upper() in w:
                    pos[k] = w[k.upper()]
            if "E" in w:
                retracted = 0.0
        elif cmd == "G20":
            warnings.append(tx("fq.gc_inches"))
    if not feat_len:
        raise ImportError_(tx("fq.gc_no_extrusion"))
    if arcs:
        warnings.append(tx("fq.gc_arcs", n=arcs))
    d_text = kv.get("filament_diameter") or kv.get("filament diameter")
    d = _first_float(d_text) if d_text else None
    if not d or d <= 0:
        d = 1.75
        warnings.append(tx("fq.gc_diameter_assumed"))
    area = math.pi * (d / 2.0) ** 2
    by_feature = {f: ln * area / 1000.0 for f, ln in feat_len.items()}
    total_len = sum(feat_len.values())
    computed_cm3 = total_len * area / 1000.0
    groups = {"part": 0.0, "skin": 0.0, "support": 0.0, "excluded": 0.0}
    for f, v in by_feature.items():
        groups[_feature_group(f)] += v
    # slicer's own figures
    t_slicer = None
    for key in ("estimated printing time (normal mode)", "total estimated time", "estimated printing time", "model printing time"):
        if key in kv:
            t_slicer = _duration_s(kv[key])
            break
    if t_slicer is None and "time" in kv and re.fullmatch(r"\d+", kv["time"].strip()):
        t_slicer = float(kv["time"])  # Cura: ;TIME:seconds
    fil_mm = next((_first_float(v) for k, v in kv.items() if k.startswith(("filament used [mm]", "total filament used [mm]"))), None)
    if fil_mm is None and "filament used" in kv and kv["filament used"].lower().endswith("m"):
        v = _first_float(kv["filament used"])
        fil_mm = v * 1000.0 if v is not None else None
    fil_cm3 = next((_first_float(v) for k, v in kv.items() if k.startswith(("filament used [cm3]", "total filament used [cm3]",
                                                                           "total filament volume [cm^3]"))), None)
    fil_g = next((_first_float(v) for k, v in kv.items() if k.startswith(("filament used [g]", "total filament used [g]"))), None)
    if fil_cm3 is None and fil_mm is not None:
        fil_cm3 = fil_mm * area / 1000.0
    layers_c = next((_first_float(kv[k]) for k in ("total layers count", "total layer number", "layer_count") if k in kv), None)
    layer_h = next((_first_float(kv[k]) for k in ("layer_height", "layer height") if k in kv), None)
    layers = int(layers_c) if layers_c else (layer_marks or len(z_levels))
    if not layers_c and not layer_marks:
        warnings.append(tx("fq.gc_layers_from_z"))
    if t_slicer is None:
        warnings.append(tx("fq.gc_no_slicer_time"))
    if fil_cm3 is not None and computed_cm3 > 0 and abs(fil_cm3 - computed_cm3) / computed_cm3 > 0.05:
        warnings.append(tx("fq.gc_volume_disagrees", slicer=f"{fil_cm3:.2f}", computed=f"{computed_cm3:.2f}"))
    if not seen_type:
        warnings.append(tx("fq.gc_no_types"))
    return {
        "format": "gcode", "slicer": slicer, "moves": moves,
        "time_h": (t_slicer / 3600.0) if t_slicer is not None else None,
        "time_h_from_moves": time_s / 3600.0,
        "time_note": tx("fq.gc_time_note"),
        "filament_diameter_mm": d, "filament_length_m": total_len / 1000.0,
        "filament_cm3": fil_cm3 if fil_cm3 is not None else computed_cm3,
        "filament_cm3_computed": computed_cm3, "filament_g": fil_g,
        "layers": layers, "layer_height_mm": layer_h, "part_height_mm": max_z or None,
        "path_length_m": path_len / 1000.0, "travel_length_m": travel_len / 1000.0,
        "has_feature_types": seen_type,
        "features_cm3": dict(sorted(by_feature.items(), key=lambda kv_: -kv_[1])),
        "groups_cm3": groups if seen_type else None,
        "extruded_cm3": (groups["part"] + groups["skin"]) if seen_type else None,
        "skin_cm3": groups["skin"] if seen_type else None,
        "support_cm3": groups["support"] if seen_type else None,
        "bbox_mm": {"x": [xmin, xmax], "y": [ymin, ymax]} if xmax > xmin else None,
        "warnings": warnings,
    }


def parse_trajectory(text: str, unit: str = "mm") -> dict:
    """Total path of a list of points. CSV (header optional, columns x,y[,z]) or JSON."""
    if not isinstance(text, str) or not text.strip():
        raise ImportError_(tx("fq.tr_empty"))
    if len(text) > MAX_BYTES:
        raise ImportError_(tx("fq.gc_too_big", max=MAX_BYTES // 1_000_000))
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4}.get((unit or "mm").lower())
    if scale is None:
        raise ImportError_(tx("fq.tr_unit", unit=unit))
    s = text.strip()
    pts: list[list[float]] = []
    try:
        if s[0] in "[{":
            data = json.loads(s)
            rows = data.get("points") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                raise ImportError_(tx("fq.tr_shape"))
            for r in rows:
                if isinstance(r, dict):
                    r = [r.get("x"), r.get("y"), r.get("z", 0.0)]
                if not isinstance(r, (list, tuple)) or len(r) < 2:
                    raise ImportError_(tx("fq.tr_shape"))
                pts.append([float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 and r[2] is not None else 0.0])
        else:
            for ln in s.splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                cells = [c for c in re.split(r"[,;\t ]+", ln) if c != ""]
                try:
                    nums = [float(c) for c in cells]
                except ValueError:
                    if not pts:
                        continue  # a header
                    raise ImportError_(tx("fq.tr_row", row=ln[:60])) from None
                if len(nums) < 2:
                    raise ImportError_(tx("fq.tr_row", row=ln[:60]))
                pts.append([nums[0], nums[1], nums[2] if len(nums) > 2 else 0.0])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, ImportError_):
            raise
        raise ImportError_(tx("fq.tr_shape")) from None
    if len(pts) < 2:
        raise ImportError_(tx("fq.tr_few"))
    if len(pts) > MAX_POINTS:
        raise ImportError_(tx("fq.tr_many", max=MAX_POINTS))
    if any(not math.isfinite(v) for p in pts for v in p):
        raise ImportError_(tx("fq.tr_shape"))
    length = 0.0
    for a, b in zip(pts, pts[1:]):
        length += math.sqrt(sum((q - p) ** 2 for p, q in zip(a, b)))
    length *= scale
    zs = sorted({round(p[2] * scale, 3) for p in pts})
    has_z = len(zs) > 1
    warnings = [] if has_z else [tx("fq.tr_flat")]
    return {"format": "trajectory", "points": len(pts), "path_length_m": length / 1000.0, "path_length_mm": length,
            "layers": len(zs) if has_z else None, "part_height_mm": (zs[-1] - zs[0]) if has_z else None,
            "bbox_mm": {"x": [min(p[0] for p in pts) * scale, max(p[0] for p in pts) * scale],
                        "y": [min(p[1] for p in pts) * scale, max(p[1] for p in pts) * scale]}, "warnings": warnings}


def parse_toolpath(content: str, kind: str = "auto", unit: str = "mm") -> dict:
    kind = (kind or "auto").lower()
    if kind not in ("auto", "gcode", "trajectory"):
        raise ImportError_(tx("fq.tp_kind", kind=kind))
    if kind == "auto":
        kind = "gcode" if re.search(r"^\s*G[01]\s+[XYZEF]", content or "", re.M | re.I) else "trajectory"
    out = parse_gcode(content) if kind == "gcode" else parse_trajectory(content, unit)
    out["sha256"] = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    return out


def measurements_for(parsed: dict, process: str, label: str = "") -> dict[str, dict]:
    """Quote parameters that this reading fills (name -> {value, source, note}). Only what the file really says."""
    src = tx("fq.tp_note", label=label or parsed["format"], sha=parsed["sha256"][:8] if parsed.get("sha256") else "")
    m: dict[str, dict] = {}

    def put(name: str, value: Any, extra: str = ""):
        if isinstance(value, (int, float)) and math.isfinite(value):
            m[name] = {"value": float(value), "source": "measured", "note": (src + (" " + extra if extra else "")).strip()}

    if parsed["format"] == "gcode":
        if process == "fdm":
            put("slicer_time_h", parsed.get("time_h"))
            if parsed.get("extruded_cm3") is not None:
                put("slicer_extruded_cm3", parsed["extruded_cm3"], tx("fq.tp_without_support"))
                put("slicer_skin_cm3", parsed["skin_cm3"])
                put("slicer_support_cm3", parsed["support_cm3"])
            else:
                put("slicer_extruded_cm3", parsed.get("filament_cm3"), tx("fq.tp_all_filament"))
                put("slicer_support_cm3", 0.0, tx("fq.tp_support_inside"))
            put("layer_height_mm", parsed.get("layer_height_mm"))
            put("part_height_mm", parsed.get("part_height_mm"))
        elif process == "concrete":
            put("toolpath_length_m", parsed.get("path_length_m"))
            put("layer_count", parsed.get("layers"))
            put("layer_height_mm", parsed.get("layer_height_mm"))
            put("part_height_mm", parsed.get("part_height_mm"))
        elif process == "cnc_laser":
            put("cut_length_mm", parsed.get("path_length_m", 0) * 1000.0)
    else:
        if process == "concrete":
            put("toolpath_length_m", parsed.get("path_length_m"))
            put("layer_count", parsed.get("layers"))
            put("part_height_mm", parsed.get("part_height_mm"))
        elif process == "cnc_laser":
            put("cut_length_mm", parsed.get("path_length_mm"))
    return m


# --------------------------------------------------------------------------------------------- Vulcan inspection
#: quote parameters that a failed Vulcan rule puts in doubt
AFFECTS = {
    "require_watertight": ["volume_cm3", "surface_cm2"],
    "min_wall_mm": ["perimeters", "line_width_mm", "surface_cm2"],
    "max_extent_mm": ["part_height_mm"],
    "units": ["volume_cm3", "surface_cm2", "part_height_mm"],
    "max_bodies": ["volume_cm3", "surface_cm2"],
}


def inspection_warnings(report: dict) -> dict:
    """Vulcan's draft report (``asset_inspect``) as structured warnings.

    Each warning: ``severity`` (error: Vulcan sees a blocker; warning: a rule fails or something is assumed; info:
    a rule could not be evaluated), ``code``, ``message``, ``affects`` (quote parameters in doubt), ``suggestion``,
    ``automatic`` (Vulcan can fix it by itself) and ``ref`` (defect or recommendation id in the report)."""
    if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
        raise ImportError_(tx("fq.insp_shape"))
    blockers = {b.get("defect"): b for b in report.get("blockers") or []}
    recs_by_defect: dict[str, list[dict]] = {}
    for r in report.get("recommendations") or []:
        recs_by_defect.setdefault(r.get("for_defect"), []).append(r)
    warnings: list[dict] = []
    for d in report.get("defects") or []:
        rec = next(iter(recs_by_defect.get(d.get("id"), [])), None)
        b = blockers.get(d.get("id"))
        warnings.append({"severity": "error" if b else "warning", "code": d.get("rule"), "message": d.get("message") or d.get("rule"),
                         "affects": AFFECTS.get(d.get("rule"), []), "observed": d.get("observed"), "limit": d.get("limit"),
                         "suggestion": rec.get("text") if rec else None, "automatic": bool(rec and rec.get("automatic")),
                         "blocker": b.get("reason") if b else None, "ref": d.get("id")})
    for r in report.get("recommendations") or []:
        if r.get("for_defect"):
            continue
        sev = "warning" if r.get("operation") in ("verify_units", "preserve_attributes") else "info"
        warnings.append({"severity": sev, "code": r.get("operation"), "message": r.get("text"),
                         "affects": AFFECTS["units"] if r.get("operation") == "verify_units" else [],
                         "suggestion": None, "automatic": bool(r.get("automatic")), "ref": r.get("id")})
    order = {"error": 0, "warning": 1, "info": 2}
    warnings.sort(key=lambda w: order[w["severity"]])
    summary = report.get("summary") or {}
    counts = {s: sum(1 for w in warnings if w["severity"] == s) for s in order}
    return {"verdict": summary.get("verdict"), "compliant": summary.get("compliant"), "fully_verified": summary.get("fully_verified"),
            "profile": (report.get("profile") or {}).get("id"), "source": report.get("source"), "counts": counts,
            "warnings": warnings, "notes": report.get("notes"),
            "texts": [f"[{w['severity']}] {w['message']}" + (f" ({', '.join(w['affects'])})" if w["affects"] else "") for w in warnings
                      if w["severity"] != "info" or counts["error"] + counts["warning"] == 0]}


def fetch_inspection(model_id: Any, target: str = "fdm-print", lang: Optional[str] = None, timeout: float = 120.0,
                     call: Optional[Callable] = None) -> dict:
    """Ask Vulcan for its inspection report of a library model through the Hub (``family.call``)."""
    lang = lang or get_lang()
    try:
        mid = int(model_id)
    except (TypeError, ValueError):
        raise ImportError_(tx("fq.vulcan_model_id")) from None
    if mid < 1:
        raise ImportError_(tx("fq.vulcan_model_id"))
    if call is None:
        from .hoard_link import family
        call = family.call
    try:
        reply = call("vulcan", "asset_inspect", {"target": target or "fdm-print", "id": mid, "lang": lang}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - the Hub or Vulcan being down is an answer, not a crash
        raise ImportError_(tx("fq.insp_unreachable", detail=str(exc)[:200])) from None
    if not isinstance(reply, dict) or not reply.get("ok"):
        raise ImportError_(tx("fq.insp_failed", detail=str((reply or {}).get("error") if isinstance(reply, dict) else reply)[:300]))
    result = reply.get("result")
    if isinstance(result, dict) and result.get("ok") is False:
        raise ImportError_(tx("fq.insp_failed", detail=str(result.get("error"))[:300]))
    return result


# --------------------------------------------------------------------------------------------- on stored quotes
def toolpath_preview(content: str, kind: str = "auto", unit: str = "mm", process: Optional[str] = None, label: str = "") -> dict:
    """What a toolpath file says, and which quote parameters it would fill. Writes nothing."""
    parsed = parse_toolpath(content, kind, unit)
    out = {"reading": parsed, "applied": False}
    if process:
        out["parameters"] = measurements_for(parsed, process, label)
    return out


def import_toolpath(services, name: str, content: str, kind: str = "auto", unit: str = "mm", label: str = "",
                    apply: bool = True, source: str = "ui") -> dict:
    """Read a G-code / trajectory and, with `apply`, write what it measures into the stored quote (source 'measured',
    with the file hash in the note) and keep the reading in the quote's provenance."""
    from . import fabquote as fq

    doc = fq._load(services, name)
    process = doc["spec"]["process"]
    out = toolpath_preview(content, kind, unit, process, label)
    params = out.get("parameters") or {}
    if not params:
        raise FabQuoteError(tx("fq.tp_nothing", process=process, kind=out["reading"]["format"]))
    out["name"] = name
    if apply:
        res = fq.set_parameters(services, name, params, source=source)
        doc = fq._load(services, name)
        reading = {k: v for k, v in out["reading"].items() if k not in ("features_cm3",)}
        doc.setdefault("provenance", {}).setdefault("toolpath", []).append(
            {"at": fq._now(services), "label": label or None, "kind": out["reading"]["format"], "sha256": out["reading"]["sha256"],
             "parameters": sorted(params), "reading": reading})
        fq._save(services, name, doc)
        out.update({"applied": True, "diff": res["diff"], "result": res["result"]})
    return out


def inspect_quote(services, name: str, model_id: Any, target: str = "fdm-print", lang: Optional[str] = None,
                  call: Optional[Callable] = None, source: str = "ui") -> dict:
    """Vulcan's inspection of a model, through the Hub, kept on the quote as structured warnings (and shown with its
    other warnings)."""
    from . import fabquote as fq

    def do():
        doc = fq._load(services, name)
        report = fetch_inspection(model_id, target, lang, call=call)
        parsed = inspection_warnings(report)
        doc["inspection"] = {"at": fq._now(services), "model_id": int(model_id), "target": target, **{k: parsed[k] for k in (
            "verdict", "compliant", "fully_verified", "counts", "warnings", "texts")}}
        fq._save(services, name, doc)
        return {"name": name, "model_id": int(model_id), "target": target, **parsed,
                "_log_summary": f"read Vulcan inspection of model {model_id} for {name!r}: {parsed['counts']}"}
    return services._log("fabquote_inspect", source, None, {"name": name, "model_id": model_id, "target": target}, do)

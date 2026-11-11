"""User-visible messages in English and Spanish (Spain).

The analysis modules build warnings, verdicts, notes and error texts for people. Each one is a catalogue entry
``KEY -> {"en": ..., "es": ...}`` written as a ``str.format`` template; ``t(key, lang, **kw)`` renders it.

* ``lang`` defaults to "en", so agent/MCP callers and the existing tests see exactly the English they always saw.
* The REST layer sets the language of the current request once (``use_lang`` / ``set_lang``); every ``t()`` call
  without an explicit ``lang`` then follows it, so the message builders do not need a ``lang`` argument threaded
  through every helper.
* In Spanish, numbers placed in a template use the decimal comma and the point as thousands separator.
* Data values (column names, activity names) are passed as arguments and never translated.
"""

from __future__ import annotations

import contextlib
import contextvars
import string
from typing import Any

LANGS = ("en", "es")
DEFAULT_LANG = "en"

_current: contextvars.ContextVar[str] = contextvars.ContextVar("nightingale_lang", default=DEFAULT_LANG)

CATALOGUE: dict[str, dict[str, str]] = {}


def norm_lang(lang: Any) -> str:
    """'es', 'es-ES', 'ES_es' -> 'es'; anything unknown -> 'en'."""
    code = str(lang or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGS else DEFAULT_LANG


def get_lang() -> str:
    return _current.get()


def set_lang(lang: Any) -> contextvars.Token:
    return _current.set(norm_lang(lang))


def reset_lang(token: contextvars.Token) -> None:
    _current.reset(token)


@contextlib.contextmanager
def use_lang(lang: Any):
    token = set_lang(lang)
    try:
        yield
    finally:
        reset_lang(token)


class _Fmt(string.Formatter):
    def __init__(self, lang: str):
        self.lang = lang

    def format_field(self, value, format_spec):
        out = super().format_field(value, format_spec)
        if self.lang == "es" and isinstance(value, (int, float)) and not isinstance(value, bool):
            out = out.translate({ord(","): ".", ord("."): ","})
        return out


def t(message: str, lang: Any = None, **kw) -> str:
    """Render message `message` in `lang` (default: the language of the current request, else English)."""
    code = norm_lang(lang) if lang is not None else _current.get()
    entry = CATALOGUE[message]
    return _Fmt(code).vformat(entry.get(code) or entry["en"], (), kw)


def register(entries: dict[str, dict[str, str]]) -> None:
    for key, val in entries.items():
        if key in CATALOGUE:
            raise KeyError(f"duplicate message key {key}")
        if set(val) != set(LANGS):
            raise KeyError(f"{key}: needs exactly {LANGS}")
        CATALOGUE[key] = val


def missing(lang: str = "es") -> list[str]:
    return [k for k, v in CATALOGUE.items() if not v.get(lang)]


# ======================================================================= process mining (pm.*)
register({
    "pm.caveat": {
        "en": "Descriptive results from the log as given; rehearsals are scenarios under the stated assumptions "
              "(first-order routing, independent durations), not predictions.",
        "es": "Resultados descriptivos del registro tal como se ha dado; los ensayos son escenarios bajo los supuestos "
              "indicados (enrutamiento de primer orden, duraciones independientes), no predicciones."},
    "pm.map_shape": {
        "en": "mapping: expected {{case, activity, timestamp, start?, resource?, cost?}}",
        "es": "mapping: se esperaba {{case, activity, timestamp, start?, resource?, cost?}}"},
    "pm.map_required": {
        "en": "mapping: {key!r} is required (use suggest to get proposals)",
        "es": "mapping: {key!r} es obligatorio (usa «sugerir» para obtener propuestas)"},
    "pm.map_notfound": {
        "en": "mapping: column {col!r} not found for {key!r}; columns: {cols}",
        "es": "mapping: no se encuentra la columna {col!r} para {key!r}; columnas: {cols}"},
    "pm.map_distinct": {
        "en": "mapping: case, activity, timestamp and start must be different columns",
        "es": "mapping: case, activity, timestamp y start deben ser columnas distintas"},
    "pm.too_many_rows": {
        "en": "{total:,} rows is above the {limit:,}-event limit; filter the dataset first",
        "es": "{total:,} filas superan el límite de {limit:,} eventos; filtra antes el conjunto de datos"},
    "pm.no_events": {
        "en": "no usable events: check the mapping (case, activity, timestamp) and the timestamp format",
        "es": "no hay eventos utilizables: revisa la asignación de columnas (caso, actividad, marca de tiempo) y el "
              "formato de la marca de tiempo"},
    "pm.no_column": {
        "en": "No column looks like {key}: pick it by hand.",
        "es": "Ninguna columna parece ser «{key}»: elígela a mano."},
    "pm.verdict_concentrated": {
        "en": "{act!r} concentrates {share:.0f}% of all waiting time (median {median:.2f} h, p90 {p90:.2f} h before "
              "the step).",
        "es": "{act!r} concentra el {share:.0f} % de toda la espera (mediana de {median:.2f} h, p90 de {p90:.2f} h "
              "antes del paso)."},
    "pm.verdict_spread": {
        "en": "Waiting time is spread out: no single step holds 30% or more of it.",
        "es": "La espera está repartida: ningún paso concentra el 30 % o más."},
    "pm.warn_few_cases": {
        "en": "Only {n} cases: variant shares and waiting statistics are very uncertain.",
        "es": "Solo hay {n} casos: las proporciones de variantes y las estadísticas de espera son muy inciertas."},
    "pm.warn_dropped": {
        "en": "{total} rows were dropped ({detail}).",
        "es": "Se descartaron {total} filas ({detail})."},
    "pm.warn_start_after_end": {
        "en": "{n} events had a start after their end; their duration was set to 0.",
        "es": "{n} eventos tenían el inicio después del fin; su duración se ha fijado en 0."},
    "pm.warn_overlaps": {
        "en": "{n} steps overlapped the previous one (parallel work); their waiting time was set to 0.",
        "es": "{n} pasos se solapaban con el anterior (trabajo en paralelo); su espera se ha fijado en 0."},
    "pm.warn_no_start": {
        "en": "No start column: the time before a step is the whole elapsed time until it finished (waiting plus "
              "working), so bottlenecks mark slow steps, not only queues.",
        "es": "Sin columna de inicio: el tiempo previo a un paso es todo el tiempo transcurrido hasta que terminó "
              "(espera más trabajo), así que los cuellos de botella señalan pasos lentos, no solo colas."},
    "pm.graph_method": {
        "en": "Heuristic-miner dependency (a>b - b>a)/(a>b + b>a + 1), self-loops n/(n+1); a pair is also kept when it "
              "forms a length-two loop with the same measure on a-b-a patterns.",
        "es": "Dependencia del minero heurístico (a>b - b>a)/(a>b + b>a + 1), bucles sobre el mismo paso n/(n+1); un "
              "par también se conserva cuando forma un bucle de longitud dos con la misma medida en patrones a-b-a."},
    "pm.ref_steps_list": {
        "en": "reference.steps: give an ordered list of steps",
        "es": "reference.steps: indica una lista ordenada de pasos"},
    "pm.ref_parallel": {
        "en": "reference.steps[{i}]: 'parallel' needs a list of at least two activities",
        "es": "reference.steps[{i}]: «parallel» necesita una lista de al menos dos actividades"},
    "pm.ref_member": {
        "en": "reference.steps[{i}]: parallel members are names or {{activity, optional}}",
        "es": "reference.steps[{i}]: los miembros en paralelo son nombres o {{activity, optional}}"},
    "pm.ref_item": {
        "en": "reference.steps[{i}]: expected a name, {{activity, optional}} or {{parallel: [...]}}",
        "es": "reference.steps[{i}]: se esperaba un nombre, {{activity, optional}} o {{parallel: [...]}}"},
    "pm.ref_twice": {
        "en": "reference.steps: {act!r} appears twice; give each step once",
        "es": "reference.steps: {act!r} aparece dos veces; indica cada paso una sola vez"},
    "pm.ref_shape": {
        "en": "reference: expected {{steps: [...]}} or {{transitions: [[a, b], ...], start?, end?}}",
        "es": "reference: se esperaba {{steps: [...]}} o {{transitions: [[a, b], ...], start?, end?}}"},
    "pm.ref_pairs": {
        "en": "reference.transitions: expected a list of [from, to] pairs",
        "es": "reference.transitions: se esperaba una lista de pares [origen, destino]"},
    "pm.ref_give": {
        "en": "reference: give 'steps' (ordered, with optional / parallel) or 'transitions'",
        "es": "reference: indica «steps» (ordenados, con opcionales o en paralelo) o «transitions»"},
    "pm.dev_reordered_before": {
        "en": "{x} before {y} (expected {y} first)",
        "es": "{x} antes de {y} (se esperaba {y} primero)"},
    "pm.dev_reordered_then": {
        "en": "{a} then {b} (expected {b} then {a})",
        "es": "{a} y luego {b} (se esperaba {b} y luego {a})"},
    "pm.dev_skipped_between": {
        "en": "between {a} and {b}: {path}",
        "es": "entre {a} y {b}: {path}"},
    "pm.warn_ref_unknown": {
        "en": "None of the reference activities appears in the log: check spelling and case.",
        "es": "Ninguna actividad de la referencia aparece en el registro: revisa la ortografía y las mayúsculas."},
    "pm.conform_method": {
        "en": "Per case: skipped = required step missing; inserted = activity not in the reference; repeated = a "
              "reference step done more than once; reordered = steps in a different slot order (inside a parallel "
              "group any order fits). With transitions: a forbidden transition is labelled reordered when the "
              "reverse is allowed, skipped when allowed steps could fill the gap, otherwise unexpected.",
        "es": "Por caso: skipped = falta un paso obligatorio; inserted = actividad que no está en la referencia; "
              "repeated = un paso de la referencia hecho más de una vez; reordered = pasos en distinto orden de "
              "posición (dentro de un grupo en paralelo vale cualquier orden). Con transiciones: una transición "
              "prohibida se etiqueta reordered si la inversa está permitida, skipped si pasos permitidos podrían "
              "llenar el hueco y unexpected en los demás casos."},
    "pm.too_few_cases": {
        "en": "{n} cases are too few to build a model (need at least {min}).",
        "es": "{n} casos son pocos para construir un modelo (se necesitan al menos {min})."},
    "pm.warn_no_duration": {
        "en": "{act!r} never has a measurable duration (it is always the first step and there is no start column); it "
              "is modelled as instantaneous.",
        "es": "{act!r} no tiene nunca una duración medible (siempre es el primer paso y no hay columna de inicio); se "
              "modela como instantáneo."},
    "pm.basis_resources": {"en": "distinct resources", "es": "recursos distintos"},
    "pm.basis_concurrency": {
        "en": "maximum observed concurrency (a lower bound)",
        "es": "máxima concurrencia observada (una cota inferior)"},
    "pm.basis_not_modelled": {
        "en": "not modelled (no start column): the step is a pure delay",
        "es": "no se modela (sin columna de inicio): el paso es un simple retardo"},
    "pm.warn_few_samples": {
        "en": "{act!r}: only {n} duration samples; its distribution is uncertain.",
        "es": "{act!r}: solo hay {n} muestras de duración; su distribución es incierta."},
    "pm.warn_shared": {
        "en": "{n} resources work on several activities; each activity is modelled with its own pool, which "
              "overstates capacity when they are shared.",
        "es": "{n} recursos trabajan en varias actividades; cada actividad se modela con su propio grupo, lo que "
              "sobrestima la capacidad cuando se comparten."},
    "pm.same_moment": {
        "en": "all cases start at the same moment: the arrival rate cannot be estimated.",
        "es": "todos los casos empiezan en el mismo instante: no se puede estimar el ritmo de llegadas."},
    "pm.arr_poisson": {
        "en": "Poisson at {rate:.4g} cases/hour (inter-arrival CV {cv:.2f}, close to 1)",
        "es": "Poisson a {rate:.4g} casos/hora (CV de los intervalos entre llegadas {cv:.2f}, cercano a 1)"},
    "pm.arr_resampled": {
        "en": "measured inter-arrival times resampled (CV {cv:.2f}, not close to 1)",
        "es": "intervalos entre llegadas medidos y remuestreados (CV {cv:.2f}, no cercano a 1)"},
    "pm.arr_measured": {
        "en": "measured inter-arrival times",
        "es": "intervalos entre llegadas medidos"},
    "pm.warn_small_model": {
        "en": "Only {n} cases: the model rests on very little data.",
        "es": "Solo hay {n} casos: el modelo se apoya en muy pocos datos."},
    "pm.warn_no_start_model": {
        "en": "No start column: capacity is not modelled, so adding a resource has no effect; map a start column to "
              "rehearse capacity changes.",
        "es": "Sin columna de inicio: la capacidad no se modela, así que añadir un recurso no tiene efecto; asigna una "
              "columna de inicio para ensayar cambios de capacidad."},
    "pm.unknown_activity": {
        "en": "unknown activity {act!r}; activities: {acts}",
        "es": "actividad desconocida {act!r}; actividades: {acts}"},
    "pm.ch_set_servers": {
        "en": "set {act} to {n} resources",
        "es": "fijar {act} en {n} recursos"},
    "pm.ch_add": {
        "en": "add {n} resource(s) at {act}",
        "es": "añadir {n} recurso(s) en {act}"},
    "pm.ch_remove": {"en": "remove step {act}", "es": "quitar el paso {act}"},
    "pm.ch_route": {
        "en": "send {share:.0f}% of {src} to {dst}",
        "es": "enviar el {share:.0f} % de {src} a {dst}"},
    "pm.ch_scale": {
        "en": "scale {act} durations by {factor}",
        "es": "escalar las duraciones de {act} por {factor}"},
    "pm.change_shape": {
        "en": "change: expected {{type: add_resource|remove_step|set_routing|scale_duration, ...}}",
        "es": "change: se esperaba {{type: add_resource|remove_step|set_routing|scale_duration, ...}}"},
    "pm.cap_not_modelled": {
        "en": "capacity of {act!r} is not modelled (no start column in the log), so adding a resource would change "
              "nothing; map a start column.",
        "es": "la capacidad de {act!r} no se modela (el registro no tiene columna de inicio), así que añadir un "
              "recurso no cambiaría nada; asigna una columna de inicio."},
    "pm.add_min": {
        "en": "add_resource: {act!r} would have {new} resources; need at least 1",
        "es": "add_resource: {act!r} tendría {new} recursos; hace falta al menos 1"},
    "pm.scale_number": {
        "en": "scale_duration: 'factor' must be a number",
        "es": "scale_duration: «factor» debe ser un número"},
    "pm.scale_positive": {
        "en": "scale_duration: factor must be greater than 0 (0.7 = 30% shorter)",
        "es": "scale_duration: el factor debe ser mayor que 0 (0,7 = un 30 % más corto)"},
    "pm.route_number": {
        "en": "set_routing: 'share' must be a number between 0 and 1",
        "es": "set_routing: «share» debe ser un número entre 0 y 1"},
    "pm.route_range": {
        "en": "set_routing: 'share' must be between 0 and 1",
        "es": "set_routing: «share» debe estar entre 0 y 1"},
    "pm.route_no_other": {
        "en": "set_routing: {act!r} has no other destination to take the remaining {rest:.0%}",
        "es": "set_routing: {act!r} no tiene otro destino que absorba el {rest:.0%} restante"},
    "pm.remove_selfloop": {
        "en": "remove_step: {act!r} only loops to itself",
        "es": "remove_step: {act!r} solo vuelve a sí mismo"},
    "pm.remove_no_start": {
        "en": "remove_step: removing {act!r} leaves no step to start with",
        "es": "remove_step: quitar {act!r} no deja ningún paso por el que empezar"},
    "pm.unknown_change": {
        "en": "unknown change type {type!r}; use add_resource, remove_step, set_routing or scale_duration",
        "es": "tipo de cambio desconocido {type!r}; usa add_resource, remove_step, set_routing o scale_duration"},
    "pm.variant_changes": {
        "en": "each variant needs a non-empty 'changes' list",
        "es": "cada variante necesita una lista «changes» no vacía"},
    "pm.baseline_ok": {
        "en": "The baseline model reproduces the observed throughput time within tolerance, so its what-ifs are worth "
              "reading.",
        "es": "El modelo base reproduce el tiempo de ciclo observado dentro de la tolerancia, así que merece la pena "
              "leer sus hipótesis."},
    "pm.baseline_bad": {
        "en": "The baseline model does NOT reproduce the observed log within tolerance: it is not representative "
              "(typical causes: a step that depends on previous steps, work calendars, shared resources, or too little "
              "data). Treat the rehearsal results as illustrative only.",
        "es": "El modelo base NO reproduce el registro observado dentro de la tolerancia: no es representativo "
              "(causas típicas: un paso que depende de los anteriores, calendarios de trabajo, recursos compartidos o "
              "pocos datos). Toma los resultados del ensayo solo como ilustrativos."},
    "pm.threshold_range": {
        "en": "threshold must be between 0 and 1",
        "es": "el umbral debe estar entre 0 y 1"},
    "pm.replications_range": {
        "en": "replications must be between 1 and 100",
        "es": "las réplicas deben estar entre 1 y 100"},
    "pm.tolerance_range": {
        "en": "tolerance must be between 0 and 1 (0.15 = 15%)",
        "es": "la tolerancia debe estar entre 0 y 1 (0,15 = 15 %)"},
    "pm.variants_shape": {
        "en": "variants: expected [{{name, changes: [...]}}]",
        "es": "variants: se esperaba [{{name, changes: [...]}}]"},
    "pm.variant_default_name": {"en": "variant {i}", "es": "variante {i}"},
    "pm.variant_unique": {
        "en": "variant names must be unique ({name!r})",
        "es": "los nombres de las variantes deben ser únicos ({name!r})"},
    "pm.variant_give": {
        "en": "variant {name!r}: give a non-empty 'changes' list",
        "es": "variante {name!r}: indica una lista «changes» no vacía"},
    "pm.sim_failed": {
        "en": "the discovered model could not be simulated: {exc}",
        "es": "no se pudo simular el modelo descubierto: {exc}"},
    "pm.sum_shorter": {
        "en": "Mean throughput time shorter: {base:.2f} h -> {variant:.2f} h ({pct:+.0f}%, paired 95% interval "
              "[{lo:.2f}, {hi:.2f}] h)",
        "es": "Tiempo de ciclo medio más corto: {base:.2f} h -> {variant:.2f} h ({pct:+.0f} %, intervalo pareado del "
              "95 % [{lo:.2f}, {hi:.2f}] h)"},
    "pm.sum_longer": {
        "en": "Mean throughput time longer: {base:.2f} h -> {variant:.2f} h ({pct:+.0f}%, paired 95% interval "
              "[{lo:.2f}, {hi:.2f}] h)",
        "es": "Tiempo de ciclo medio más largo: {base:.2f} h -> {variant:.2f} h ({pct:+.0f} %, intervalo pareado del "
              "95 % [{lo:.2f}, {hi:.2f}] h)"},
    "pm.sum_same": {
        "en": "Mean throughput time is not distinguishable from the baseline (paired 95% interval of the difference "
              "includes 0)",
        "es": "El tiempo de ciclo medio no se distingue de la línea base (el intervalo pareado del 95 % de la "
              "diferencia incluye el 0)"},
    "pm.rehearse_method": {
        "en": "The log gives an empirical routing chain, per-step duration samples, resource pools and arrival times; "
              "the scenario engine simulates it over replications. Variants share the seed (common random numbers), "
              "so differences are paired by replication; 'distinguishable' means their 95% interval excludes 0.",
        "es": "El registro aporta una cadena de enrutamiento empírica, muestras de duración por paso, grupos de "
              "recursos y tiempos de llegada; el motor de escenarios la simula en varias réplicas. Las variantes "
              "comparten la semilla (números aleatorios comunes), de modo que las diferencias se emparejan por "
              "réplica; «distinguible» significa que su intervalo del 95 % excluye el 0."},
})

register({
    "pm.drop_no_case": {"en": "missing case or activity", "es": "falta el caso o la actividad"},
    "pm.drop_no_time": {"en": "missing or unreadable timestamp", "es": "marca de tiempo ausente o ilegible"},
    "pm.drop_no_start": {"en": "missing or unreadable start", "es": "inicio ausente o ilegible"},
})


# ======================================================================= scenarios (sc.*)
register({
    "sc.caveat": {
        "en": "Scenarios under the stated assumptions, not predictions: results depend on the model, the seed and any "
              "calibration sample.",
        "es": "Escenarios bajo los supuestos indicados, no predicciones: los resultados dependen del modelo, de la "
              "semilla y de la muestra de calibración."},
    "sc.missing": {"en": "{where}: missing {key!r}", "es": "{where}: falta {key!r}"},
    "sc.not_number": {"en": "{where}: {key!r} must be a number", "es": "{where}: {key!r} debe ser un número"},
    "sc.must_positive": {
        "en": "{where}: {key!r} must be greater than 0", "es": "{where}: {key!r} debe ser mayor que 0"},
    "sc.must_nonneg": {
        "en": "{where}: {key!r} must be not negative", "es": "{where}: {key!r} no puede ser negativo"},
    "sc.dist_shape": {
        "en": "{where}: expected {{\"dist\": one of {dists}, ...}}",
        "es": "{where}: se esperaba {{\"dist\": una de {dists}, ...}}"},
    "sc.max_below_min": {"en": "{where}: max is below min", "es": "{where}: el máximo es menor que el mínimo"},
    "sc.tri_order": {
        "en": "{where}: need min <= mode <= max and max > min",
        "es": "{where}: se necesita min <= mode <= max y max > min"},
    "sc.emp_needs": {
        "en": "{where}: empirical needs 'values' (a list) or 'dataset' and 'column'",
        "es": "{where}: «empirical» necesita «values» (una lista) o «dataset» y «column»"},
    "sc.emp_numbers": {
        "en": "{where}: empirical values must be numbers", "es": "{where}: los valores empíricos deben ser números"},
    "sc.emp_finite": {
        "en": "{where}: empirical values must be finite and not negative",
        "es": "{where}: los valores empíricos deben ser finitos y no negativos"},
    "sc.unknown_dist": {
        "en": "{where}: unknown distribution {dist!r}; use one of {dists}",
        "es": "{where}: distribución desconocida {dist!r}; usa una de {dists}"},
    "sc.ovr_noitem": {
        "en": "override path {path!r}: no item {part!r}",
        "es": "ruta de sustitución {path!r}: no hay ningún elemento {part!r}"},
    "sc.ovr_notcont": {
        "en": "override path {path!r}: {part!r} is not a container",
        "es": "ruta de sustitución {path!r}: {part!r} no es un contenedor"},
    "sc.ovr_nopath": {
        "en": "override path {path!r} does not exist", "es": "la ruta de sustitución {path!r} no existe"},
    "sc.model_object": {"en": "model must be a JSON object", "es": "el modelo debe ser un objeto JSON"},
    "sc.horizon": {
        "en": "horizon_hours must be greater than 0", "es": "horizon_hours debe ser mayor que 0"},
    "sc.warmup": {
        "en": "warmup_hours must be at least 0 and below horizon_hours",
        "es": "warmup_hours debe ser al menos 0 y menor que horizon_hours"},
    "sc.arr_shape": {
        "en": "arrivals: expected {{\"type\": \"poisson\" | \"schedule\" | \"empirical\", ...}}",
        "es": "arrivals: se esperaba {{\"type\": \"poisson\" | \"schedule\" | \"empirical\", ...}}"},
    "sc.arr_rates": {
        "en": "arrivals: schedule needs 'rates', a list of rates per hour (24 = by hour of day)",
        "es": "arrivals: «schedule» necesita «rates», una lista de tasas por hora (24 = por hora del día)"},
    "sc.arr_rates_num": {
        "en": "arrivals: schedule rates must be numbers", "es": "arrivals: las tasas de «schedule» deben ser números"},
    "sc.arr_rates_pos": {
        "en": "arrivals: schedule rates must be not negative and at least one above 0",
        "es": "arrivals: las tasas de «schedule» no pueden ser negativas y al menos una debe superar 0"},
    "sc.arr_emp_mean": {
        "en": "arrivals: empirical inter-arrival times must have a mean above 0",
        "es": "arrivals: los intervalos entre llegadas empíricos deben tener una media mayor que 0"},
    "sc.arr_unknown": {"en": "arrivals: unknown type {kind!r}", "es": "arrivals: tipo desconocido {kind!r}"},
    "sc.jt_list": {"en": "job_types must be a list", "es": "job_types debe ser una lista"},
    "sc.jt_mix": {
        "en": "job_types: mix values must be not negative and sum above 0",
        "es": "job_types: los valores de mix no pueden ser negativos y deben sumar más de 0"},
    "sc.jt_scale": {
        "en": "job_types: service_scale must be above 0", "es": "job_types: service_scale debe ser mayor que 0"},
    "sc.st_one": {
        "en": "stations: at least one station is required", "es": "stations: se necesita al menos una estación"},
    "sc.st_object": {"en": "stations[{i}] must be an object", "es": "stations[{i}] debe ser un objeto"},
    "sc.st_default": {"en": "Station {i}", "es": "Estación {i}"},
    "sc.st_unique": {
        "en": "station names must be unique ({name!r})",
        "es": "los nombres de las estaciones deben ser únicos ({name!r})"},
    "sc.where_station": {"en": "station {name!r}", "es": "estación {name!r}"},
    "sc.where_service": {"en": "{where} service", "es": "{where}, servicio"},
    "sc.st_servers": {
        "en": "{where}: servers must be a whole number of at least 1",
        "es": "{where}: servers debe ser un número entero de al menos 1"},
    "sc.st_shifts": {
        "en": "{where}: shifts need from/to hours in 0-24 and servers >= 0",
        "es": "{where}: shifts necesita horas from/to entre 0 y 24 y servers >= 0"},
    "sc.st_buffer": {
        "en": "{where}: buffer must be a whole number >= 0 or null",
        "es": "{where}: buffer debe ser un número entero >= 0 o null"},
    "sc.st_onfull": {
        "en": "{where}: on_full must be 'lose' or 'block'", "es": "{where}: on_full debe ser «lose» o «block»"},
    "sc.inv_object": {"en": "inventory must be an object", "es": "inventory debe ser un objeto"},
    "sc.inv_station": {
        "en": "inventory: unknown station {which!r}", "es": "inventory: estación desconocida {which!r}"},
    "sc.inv_perjob": {"en": "inventory: per_job must be above 0", "es": "inventory: per_job debe ser mayor que 0"},
    "sc.inv_qty": {
        "en": "inventory: order_quantity is smaller than per_job; jobs could never start",
        "es": "inventory: order_quantity es menor que per_job; los trabajos nunca podrían empezar"},
    "sc.reps_range": {
        "en": "replications must be between 1 and {max}", "es": "las réplicas deben estar entre 1 y {max}"},
    "sc.too_large": {
        "en": "model too large for one run (about {n:,} random draws); shorten the horizon or the replications",
        "es": "el modelo es demasiado grande para una ejecución (unos {n:,} sorteos aleatorios); acorta el horizonte "
              "o las réplicas"},
    "sc.share_object": {
        "en": "{where}: expected an object {{station: share}}",
        "es": "{where}: se esperaba un objeto {{estación: proporción}}"},
    "sc.share_number": {
        "en": "{where}: share of {name!r} must be a number",
        "es": "{where}: la proporción de {name!r} debe ser un número"},
    "sc.share_neg": {
        "en": "{where}: share of {name!r} must not be negative",
        "es": "{where}: la proporción de {name!r} no puede ser negativa"},
    "sc.share_unknown": {"en": "{where}: unknown station {name!r}", "es": "{where}: estación desconocida {name!r}"},
    "sc.share_sum": {
        "en": "{where}: shares must sum to more than 0", "es": "{where}: las proporciones deben sumar más de 0"},
    "sc.routing_shape": {
        "en": "routing: expected {{\"start\": {{station: share}}, \"next\": {{station: {{station|END: share}}}}}}",
        "es": "routing: se esperaba {{\"start\": {{estación: proporción}}, \"next\": {{estación: "
              "{{estación|END: proporción}}}}}}"},
    "sc.routing_visits": {
        "en": "routing: max_visits must be a whole number between 1 and 20",
        "es": "routing: max_visits debe ser un número entero entre 1 y 20"},
    "sc.routing_next": {
        "en": "routing.next: unknown station {name!r}", "es": "routing.next: estación desconocida {name!r}"},
    "sc.littles_ok": {
        "en": "WIP matches throughput x lead time within 10%.",
        "es": "Los casos en curso coinciden con rendimiento x tiempo de ciclo dentro del 10 %."},
    "sc.littles_bad": {
        "en": "WIP and throughput x lead time differ by more than 10%: the horizon may be too short or the warm-up too "
              "small for a steady state.",
        "es": "Los casos en curso y rendimiento x tiempo de ciclo difieren en más del 10 %: puede que el horizonte sea "
              "demasiado corto o el calentamiento demasiado pequeño para llegar a un régimen estable."},
    "sc.warn_util": {
        "en": "Station {name!r} is above 95% utilised: queues grow without bound and results depend on the horizon.",
        "es": "La estación {name!r} está utilizada por encima del 95 %: las colas crecen sin límite y los resultados "
              "dependen del horizonte."},
    "sc.warn_cut": {
        "en": "About {n:.1f} job routes per replication were cut at max_visits ({k}); raise routing.max_visits if "
              "rework can repeat more often.",
        "es": "Unas {n:.1f} rutas de trabajo por réplica se cortaron en max_visits ({k}); sube routing.max_visits si el "
              "retrabajo puede repetirse más veces."},
    "sc.warn_few_reps": {
        "en": "Fewer than 5 replications: the intervals are very wide or missing.",
        "es": "Menos de 5 réplicas: los intervalos son muy anchos o no existen."},
    "sc.variants_give": {
        "en": "variants: give at least one {{name, overrides}}",
        "es": "variants: indica al menos una variante {{name, overrides}}"},
    "sc.variant_default": {"en": "variant {i}", "es": "variante {i}"},
    "sc.variant_unique": {
        "en": "variant names must be unique ({name!r})",
        "es": "los nombres de las variantes deben ser únicos ({name!r})"},
    "sc.compare_method": {
        "en": "Common random numbers: every variant uses the same seed, so arrivals and the underlying service draws "
              "are shared; deltas are paired by replication. 'Distinguishable' means the 95% interval of the paired "
              "difference excludes 0.",
        "es": "Números aleatorios comunes: todas las variantes usan la misma semilla, así que las llegadas y los "
              "sorteos de servicio subyacentes se comparten; las diferencias se emparejan por réplica. "
              "«Distinguible» significa que el intervalo del 95 % de la diferencia pareada excluye el 0."},
    "sc.values_range": {
        "en": "values: give between 1 and 40 values", "es": "values: indica entre 1 y 40 valores"},
    "sc.warn_sample_tiny": {
        "en": "{what}: only {n} observations. This is too little to describe the real variability; treat any scenario "
              "built on it as illustrative.",
        "es": "{what}: solo hay {n} observaciones. Es muy poco para describir la variabilidad real; toma como "
              "ilustrativo cualquier escenario construido sobre ellas."},
    "sc.warn_sample_small": {
        "en": "{what}: {n} observations is a small sample; the fitted shape and the tails are uncertain.",
        "es": "{what}: {n} observaciones son una muestra pequeña; la forma ajustada y las colas son inciertas."},
    "sc.fit_none": {"en": "{what}: no usable values", "es": "{what}: no hay valores utilizables"},
    "sc.sugg_empirical": {
        "en": "enough observations to resample them directly",
        "es": "hay observaciones suficientes para remuestrearlas directamente"},
    "sc.sugg_exponential": {
        "en": "coefficient of variation {cv:.2f} is close to 1",
        "es": "el coeficiente de variación {cv:.2f} es cercano a 1"},
    "sc.sugg_smooth": {
        "en": "small sample; a smooth shape is steadier than resampling a few points",
        "es": "muestra pequeña; una forma suave es más estable que remuestrear unos pocos puntos"},
    "sc.warn_hourly": {
        "en": "Hourly schedule assumes every hour of the day has the same pattern on every day; weekdays and seasons "
              "are not separated.",
        "es": "El perfil horario supone que cada hora del día sigue el mismo patrón todos los días; no se separan los "
              "días de la semana ni las estaciones."},
    "sc.warn_days": {
        "en": "Only {days} calendar days of data: the hourly pattern rests on very few repetitions of each hour.",
        "es": "Solo hay {days} días naturales de datos: el patrón horario se apoya en muy pocas repeticiones de cada "
              "hora."},
    "sc.model_name": {
        "en": "model name: 1-60 letters, digits, spaces, '_', '.' or '-'",
        "es": "nombre del modelo: de 1 a 60 letras, dígitos, espacios, «_», «.» o «-»"},
    "sc.no_column": {
        "en": "dataset {dataset!r} has no column {column!r}; columns: {cols}",
        "es": "el conjunto de datos {dataset!r} no tiene la columna {column!r}; columnas: {cols}"},
    "sc.unit": {"en": "unit must be one of {units}", "es": "la unidad debe ser una de {units}"},
    "sc.no_values_in": {
        "en": "{path}: no usable values in {dataset}.{column}",
        "es": "{path}: no hay valores utilizables en {dataset}.{column}"},
    "sc.bad_json": {"en": "model is not valid JSON: {exc}", "es": "el modelo no es un JSON válido: {exc}"},
    "sc.give_model": {
        "en": "give 'name' (a saved model) or 'model' (inline JSON)",
        "es": "indica «name» (un modelo guardado) o «model» (JSON en línea)"},
    "sc.unknown_model_saved": {
        "en": "unknown scenario model {name!r}; saved: {saved}",
        "es": "modelo de escenario desconocido {name!r}; guardados: {saved}"},
    "sc.unknown_model": {
        "en": "unknown scenario model {name!r}", "es": "modelo de escenario desconocido {name!r}"},
    "sc.kind": {
        "en": "kind must be 'service', 'interarrival' or 'timestamps'",
        "es": "kind debe ser «service», «interarrival» o «timestamps»"},
})


# ======================================================================= shared fitting helpers (fit.*)
register({
    "fit.no_column": {
        "en": "dataset {dataset!r} has no column {column!r}; columns: {cols}",
        "es": "el conjunto de datos {dataset!r} no tiene la columna {column!r}; columnas: {cols}"},
    "fit.cluster_groups": {
        "en": "cluster covariance needs groups", "es": "la covarianza por conglomerados necesita grupos"},
    "fit.unknown_cov": {
        "en": "unknown covariance {cov!r}", "es": "covarianza desconocida {cov!r}"},
})

# ======================================================================= digital twin (tw.*)
register({
    "tw.caveat": {
        "en": "A twin is a linear model fitted to past measurements. It is only trustworthy for inputs inside the "
              "range it saw, over horizons no longer than the one validated, while the process behaves as it did in "
              "the record. Intervals cover coefficient uncertainty and residual noise, not structural error or "
              "process changes.",
        "es": "Un gemelo es un modelo lineal ajustado a mediciones pasadas. Solo es fiable con entradas dentro del "
              "rango que vio, en horizontes no más largos que el validado y mientras el proceso se comporte como en el "
              "registro. Los intervalos cubren la incertidumbre de los coeficientes y el ruido residual, no el error "
              "estructural ni los cambios del proceso."},
    "tw.nrmse_def": {
        "en": "RMSE divided by the range (max - min) of the observed validation values",
        "es": "RMSE dividido por el rango (máx - mín) de los valores de validación observados"},
    "tw.few_rows": {
        "en": "only {n} usable rows; a twin needs at least {need}",
        "es": "solo hay {n} filas utilizables; un gemelo necesita al menos {need}"},
    "tw.time_increasing": {
        "en": "the time column has no increasing values", "es": "la columna de tiempo no tiene valores crecientes"},
    "tw.short_stretch": {
        "en": "the longest regularly sampled stretch has {seg} rows; need at least {need}. Check gaps, duplicate "
              "timestamps and missing values.",
        "es": "el tramo más largo con muestreo regular tiene {seg} filas; se necesitan al menos {need}. Revisa los "
              "huecos, las marcas de tiempo duplicadas y los valores ausentes."},
    "tw.warn_left_out": {
        "en": "{left} of {n} rows were left out (missing values, gaps or irregular sampling); the twin uses the "
              "longest regular stretch of {seg} rows.",
        "es": "Se dejaron fuera {left} de {n} filas (valores ausentes, huecos o muestreo irregular); el gemelo usa el "
              "tramo regular más largo, de {seg} filas."},
    "tw.lag_few": {
        "en": "lag {p}: {rows} usable rows for {nreg} coefficients per output is too few",
        "es": "retardo {p}: {rows} filas utilizables para {nreg} coeficientes por salida son pocas"},
    "tw.no_stable_lag": {
        "en": "no lag order produced a stable model on the selection block; the data may be too short or too noisy for "
              "a linear dynamic model",
        "es": "ningún orden de retardo produjo un modelo estable en el bloque de selección; puede que los datos sean "
              "demasiado cortos o ruidosos para un modelo dinámico lineal"},
    "tw.input_flat": {
        "en": "the input does not change in the training block: a step response cannot be fitted",
        "es": "la entrada no cambia en el bloque de entrenamiento: no se puede ajustar una respuesta a un escalón"},
    "tw.fopdt_noconv": {
        "en": "the first-order-plus-dead-time fit did not converge",
        "es": "el ajuste de primer orden con tiempo muerto no convergió"},
    "tw.model_kind": {
        "en": "model must be 'arx', 'fopdt' or 'auto'", "es": "model debe ser «arx», «fopdt» o «auto»"},
    "tw.need_input": {
        "en": "give at least one input (control) column", "es": "indica al menos una columna de entrada (control)"},
    "tw.need_output": {
        "en": "give at least one output column", "es": "indica al menos una columna de salida"},
    "tw.valfrac": {
        "en": "validation_fraction must be between 0.1 and 0.5",
        "es": "validation_fraction debe estar entre 0,1 y 0,5"},
    "tw.lagstart": {
        "en": "input_lag_start must be 0, 1, 2 or 3", "es": "input_lag_start debe ser 0, 1, 2 o 3"},
    "tw.fopdt_siso": {
        "en": "the first-order-plus-dead-time model needs exactly one input and one output",
        "es": "el modelo de primer orden con tiempo muerto necesita exactamente una entrada y una salida"},
    "tw.holdout": {
        "en": "after holding out {nval} rows only {ntr} remain for fitting; need at least {need}",
        "es": "tras reservar {nval} filas quedan solo {ntr} para ajustar; se necesitan al menos {need}"},
    "tw.input_const": {
        "en": "input {name!r} does not vary in the fitting block: its effect cannot be identified",
        "es": "la entrada {name!r} no varía en el bloque de ajuste: no se puede identificar su efecto"},
    "tw.output_const": {
        "en": "output {name!r} is constant in the fitting block",
        "es": "la salida {name!r} es constante en el bloque de ajuste"},
    "tw.warn_pole": {
        "en": "The ARX model has a pole of magnitude {mag:.3f} (>= 1): it is unstable or integrating, so long "
              "simulations will diverge or drift.",
        "es": "El modelo ARX tiene un polo de módulo {mag:.3f} (>= 1): es inestable o integrador, así que las "
              "simulaciones largas divergirán o derivarán."},
    "tw.warn_boot_stable": {
        "en": "Only {n} of {total} bootstrap refits were stable: parameter intervals are unreliable.",
        "es": "Solo {n} de {total} reajustes bootstrap fueron estables: los intervalos de los parámetros no son "
              "fiables."},
    "tw.warn_gain_undef": {
        "en": "The steady-state gain is undefined (a pole at 1: the output integrates its input).",
        "es": "La ganancia en régimen estacionario no está definida (un polo en 1: la salida integra su entrada)."},
    "tw.warn_auto": {
        "en": "Auto selection on the held-out block: ARX free-run NRMSE {a:.4f}, FOPDT {f:.4f}; using {chosen} (the "
              "simpler model wins ties within 5%).",
        "es": "Selección automática en el bloque reservado: NRMSE en simulación libre de ARX {a:.4f}, de FOPDT "
              "{f:.4f}; se usa {chosen} (el modelo más simple gana los empates dentro del 5 %)."},
    "tw.warn_resid_arx": {
        "en": "Residuals of {out} are autocorrelated (Ljung-Box p={p:.3g}): the model misses dynamics or "
              "disturbances, so its intervals are too narrow.",
        "es": "Los residuos de {out} están autocorrelados (Ljung-Box p={p:.3g}): el modelo no capta dinámicas o "
              "perturbaciones, así que sus intervalos son demasiado estrechos."},
    "tw.warn_coverage": {
        "en": "The {level}% interval for {out} covered only {cov:.0%} of the held-out block: treat the what-if "
              "intervals as optimistic.",
        "es": "El intervalo del {level} % de {out} cubrió solo el {cov:.0%} del bloque reservado: considera "
              "optimistas los intervalos de los escenarios hipotéticos."},
    "tw.warn_boot_conv": {
        "en": "Only {n} bootstrap refits converged: parameter intervals are unreliable.",
        "es": "Solo {n} reajustes bootstrap convergieron: los intervalos de los parámetros no son fiables."},
    "tw.warn_deadtime": {
        "en": "The estimated dead time is shorter than one sampling step: it is not distinguishable from zero.",
        "es": "El tiempo muerto estimado es menor que un paso de muestreo: no se distingue de cero."},
    "tw.warn_tau": {
        "en": "The time constant is longer than a third of the record: the response never settled, so the gain is "
              "extrapolated from an unfinished response.",
        "es": "La constante de tiempo es mayor que un tercio del registro: la respuesta nunca se estabilizó, así que "
              "la ganancia se extrapola a partir de una respuesta inacabada."},
    "tw.boot_method": {"en": "moving-block residual bootstrap", "es": "bootstrap de residuos por bloques móviles"},
    "tw.resid_note": {
        "en": "free-run residuals (observed minus simulated response) over the fitting block",
        "es": "residuos en simulación libre (respuesta observada menos simulada) sobre el bloque de ajuste"},
    "tw.warn_resid_fopdt": {
        "en": "Residuals of {out} are autocorrelated (Ljung-Box p={p:.3g}): a first-order model with dead time leaves "
              "dynamics unexplained; consider the ARX model.",
        "es": "Los residuos de {out} están autocorrelados (Ljung-Box p={p:.3g}): un modelo de primer orden con "
              "tiempo muerto deja dinámicas sin explicar; considera el modelo ARX."},
    "tw.warn_nrmse": {
        "en": "Free-run NRMSE for {out} on the held-out block is {v:.2f} (> 0.2): the twin is a poor predictor of "
              "this output beyond one step.",
        "es": "El NRMSE en simulación libre de {out} en el bloque reservado es {v:.2f} (> 0,2): el gemelo predice mal "
              "esta salida más allá de un paso."},
    "tw.warn_r2": {
        "en": "The twin predicts {out} worse than its mean on the held-out block (R2 {v:.2f}).",
        "es": "El gemelo predice {out} peor que su media en el bloque reservado (R2 {v:.2f})."},
    "tw.unknown_input": {
        "en": "unknown input {name!r}; the twin's inputs are {inputs}",
        "es": "entrada desconocida {name!r}; las entradas del gemelo son {inputs}"},
    "tw.profile_empty": {"en": "profile for {name!r} is empty", "es": "el perfil de {name!r} está vacío"},
    "tw.profile_form": {
        "en": "profile for {name!r}: use a number, a list, {{'step': {{'at','from','to'}}}} or "
              "{{'ramp': {{'from','to'}}}}",
        "es": "perfil de {name!r}: usa un número, una lista, {{'step': {{'at','from','to'}}}} o "
              "{{'ramp': {{'from','to'}}}}"},
    "tw.profile_finite": {
        "en": "the input profile contains non-finite values", "es": "el perfil de entrada contiene valores no finitos"},
    "tw.no_extrapolate": {
        "en": "refusing to extrapolate: input {name!r} reaches {reached:.6g}, outside the training range {lo:.6g} to "
              "{hi:.6g} by {excess:.0%} of that range (limit {limit:.0%}). A linear model fitted inside the range says "
              "nothing reliable beyond it. Collect data there, or pass allow_extrapolation=true to see the "
              "(unreliable) result.",
        "es": "me niego a extrapolar: la entrada {name!r} llega a {reached:.6g}, fuera del rango de entrenamiento de "
              "{lo:.6g} a {hi:.6g}, en un {excess:.0%} de ese rango (límite {limit:.0%}). Un modelo lineal ajustado "
              "dentro del rango no dice nada fiable más allá. Recoge datos ahí o pasa allow_extrapolation=true para "
              "ver el resultado (poco fiable)."},
    "tw.steps_range": {
        "en": "steps must be between 1 and 5000", "es": "steps debe estar entre 1 y 5000"},
    "tw.initial_unknown": {
        "en": "initial: unknown name {name!r}", "es": "initial: nombre desconocido {name!r}"},
    "tw.warn_extrap": {
        "en": "Some requested inputs lie outside the training range: the response there is extrapolated and its "
              "intervals understate the real uncertainty.",
        "es": "Algunas entradas pedidas quedan fuera del rango de entrenamiento: la respuesta ahí es una "
              "extrapolación y sus intervalos subestiman la incertidumbre real."},
    "tw.warn_leaves": {
        "en": "The predicted {out} leaves the range seen in training ({lo:.6g} to {hi:.6g}) by more than 10%: the "
              "linear model is probably no longer adequate there.",
        "es": "La {out} predicha sale del rango visto en el entrenamiento ({lo:.6g} a {hi:.6g}) en más de un 10 %: "
              "probablemente el modelo lineal ya no sea adecuado ahí."},
    "tw.warn_horizon": {
        "en": "The horizon ({steps} steps) is longer than the held-out block the twin was validated on ({val} steps): "
              "coverage of the interval beyond it is untested.",
        "es": "El horizonte ({steps} pasos) es más largo que el bloque reservado con el que se validó el gemelo "
              "({val} pasos): la cobertura del intervalo más allá no se ha comprobado."},
    "tw.name": {
        "en": "twin name: 1-60 letters, digits, spaces, '_', '.' or '-'",
        "es": "nombre del gemelo: de 1 a 60 letras, dígitos, espacios, «_», «.» o «-»"},
    "tw.unknown_saved": {
        "en": "unknown twin {name!r}; saved: {saved}", "es": "gemelo desconocido {name!r}; guardados: {saved}"},
    "tw.unknown": {"en": "unknown twin {name!r}", "es": "gemelo desconocido {name!r}"},
    "tw.in_and_out": {
        "en": "a column cannot be both an input and an output",
        "es": "una columna no puede ser a la vez entrada y salida"},
})

register({
    "tw.warn_no_time": {
        "en": "No time column given: rows are taken as equally spaced steps in the order stored.",
        "es": "No se indicó columna de tiempo: las filas se toman como pasos equiespaciados en el orden almacenado."},
    "tw.baseline_desc": {
        "en": "inputs held at their last value", "es": "entradas mantenidas en su último valor"},
    "tw.criterion": {
        "en": "free-run NRMSE on a selection block kept apart from the held-out block",
        "es": "NRMSE en simulación libre sobre un bloque de selección separado del bloque reservado"},
    "tw.form_arx": {
        "en": "multi-output ARX (linear state-space in companion form)",
        "es": "ARX multisalida (espacio de estados lineal en forma compañera)"},
    "tw.form_fopdt": {
        "en": "first order plus dead time: y = offset + gain * (1 - exp(-(t - dead_time)/time_constant)) per unit "
              "input step",
        "es": "primer orden con tiempo muerto: y = offset + gain * (1 - exp(-(t - dead_time)/time_constant)) por "
              "cada escalón unitario de entrada"},
})


# ======================================================================= experiments (ex.*)
register({
    "ex.need_factor": {
        "en": "give at least one factor: [{{name, low, high}}]",
        "es": "indica al menos un factor: [{{name, low, high}}]"},
    "ex.max_factors": {"en": "at most 12 factors per design", "es": "como máximo 12 factores por diseño"},
    "ex.factor_fields": {
        "en": "every factor needs name, low and high", "es": "cada factor necesita name, low y high"},
    "ex.factor_name": {
        "en": "factor name {name!r}: use letters, digits and '_' (it must start with a letter) so constraints can "
              "name it",
        "es": "nombre de factor {name!r}: usa letras, dígitos y «_» (debe empezar por una letra) para que las "
              "restricciones puedan nombrarlo"},
    "ex.factor_twice": {"en": "factor {name!r} is listed twice", "es": "el factor {name!r} aparece dos veces"},
    "ex.factor_bounds": {
        "en": "factor {name!r}: high must be greater than low",
        "es": "factor {name!r}: high debe ser mayor que low"},
    "ex.constraint_unknown": {
        "en": "constraint names unknown factor {name!r}; factors: {factors}",
        "es": "la restricción nombra un factor desconocido {name!r}; factores: {factors}"},
    "ex.constraint_ops": {
        "en": "constraints support comparisons, and/or/not and + - * / **",
        "es": "las restricciones admiten comparaciones, and/or/not y + - * / **"},
    "ex.constraint_ops_long": {
        "en": "constraints support comparisons, and/or/not and + - * / ** over factor names and numbers",
        "es": "las restricciones admiten comparaciones, and/or/not y + - * / ** sobre nombres de factores y números"},
    "ex.constraint_invalid": {
        "en": "constraint {item!r} is not a valid expression: {msg}",
        "es": "la restricción {item!r} no es una expresión válida: {msg}"},
    "ex.box_unknown": {
        "en": "forbidden box names unknown factor {name!r}",
        "es": "la caja prohibida nombra un factor desconocido {name!r}"},
    "ex.forbidden_form": {
        "en": "a forbidden combination is an expression string or a {{factor: [low, high]}} box",
        "es": "una combinación prohibida es una expresión de texto o una caja {{factor: [low, high]}}"},
    "ex.few_base": {
        "en": "a 2^({k}-{p}) design leaves too few base factors",
        "es": "un diseño 2^({k}-{p}) deja muy pocos factores base"},
    "ex.full_min": {
        "en": "a 2-level factorial needs at least 2 factors",
        "es": "un factorial de 2 niveles necesita al menos 2 factores"},
    "ex.full_max": {
        "en": "a full 2-level factorial above 10 factors has over a thousand runs; use 'fractional'",
        "es": "un factorial completo de 2 niveles con más de 10 factores supera el millar de ejecuciones; usa "
              "«fractional»"},
    "ex.frac_min": {
        "en": "a fractional factorial needs at least 3 factors; use 'full'",
        "es": "un factorial fraccionado necesita al menos 3 factores; usa «full»"},
    "ex.frac_max": {
        "en": "fractional designs are supported up to 10 factors",
        "es": "los diseños fraccionados se admiten hasta 10 factores"},
    "ex.fraction_range": {
        "en": "fraction must be between 1 and {max} for {k} factors",
        "es": "fraction debe estar entre 1 y {max} para {k} factores"},
    "ex.no_resolution": {
        "en": "no fraction of {k} factors reaches resolution {res}",
        "es": "ninguna fracción de {k} factores alcanza la resolución {res}"},
    "ex.warn_smallest": {
        "en": "The smallest design with resolution >= {res} has {n} runs, above your limit of {limit}.",
        "es": "El diseño más pequeño con resolución >= {res} tiene {n} ejecuciones, por encima de tu límite de "
              "{limit}."},
    "ex.res3": {
        "en": "Resolution III: main effects are aliased with two-factor interactions; use only for screening, "
              "assuming interactions are negligible.",
        "es": "Resolución III: los efectos principales están confundidos con interacciones de dos factores; úsala "
              "solo para cribado, suponiendo que las interacciones son despreciables."},
    "ex.res4": {
        "en": "Resolution IV: main effects are clear of two-factor interactions, but two-factor interactions are "
              "aliased with each other.",
        "es": "Resolución IV: los efectos principales están libres de interacciones de dos factores, pero estas "
              "están confundidas entre sí."},
    "ex.res5": {
        "en": "Resolution V: main effects and two-factor interactions are clear of each other.",
        "es": "Resolución V: los efectos principales y las interacciones de dos factores están libres unos de otros."},
    "ex.res_other": {"en": "Resolution {r}.", "es": "Resolución {r}."},
    "ex.warn_res3": {
        "en": "Resolution III: main effects are confounded with two-factor interactions.",
        "es": "Resolución III: los efectos principales están confundidos con interacciones de dos factores."},
    "ex.ccd_range": {
        "en": "the central composite design supports 2 to 6 factors",
        "es": "el diseño central compuesto admite de 2 a 6 factores"},
    "ex.ccd_variant": {
        "en": "variant must be 'face', 'inscribed' or 'circumscribed'",
        "es": "variant debe ser «face», «inscribed» o «circumscribed»"},
    "ex.warn_circ": {
        "en": "Circumscribed design: axial points sit at +-{alpha:.3f} of the half-range, outside the stated bounds. "
              "Use 'face' or 'inscribed' if the bounds are hard limits.",
        "es": "Diseño circunscrito: los puntos axiales están a +-{alpha:.3f} del semirrango, fuera de los límites "
              "indicados. Usa «face» o «inscribed» si los límites son estrictos."},
    "ex.lhs_runs": {
        "en": "lhs needs between {lo} and 2000 runs", "es": "lhs necesita entre {lo} y 2000 ejecuciones"},
    "ex.lhs_pool": {
        "en": "only {ok} of {pool} sampled points ({share:.0%}) satisfy the constraints; cannot place {n} runs. Relax "
              "the forbidden region or ask for fewer runs.",
        "es": "solo {ok} de {pool} puntos muestreados ({share:.0%}) cumplen las restricciones; no se pueden colocar "
              "{n} ejecuciones. Relaja la región prohibida o pide menos ejecuciones."},
    "ex.kind": {
        "en": "kind must be 'full', 'fractional', 'ccd' or 'lhs'",
        "es": "kind debe ser «full», «fractional», «ccd» o «lhs»"},
    "ex.warn_removed": {
        "en": "{n} of {total} planned runs hit a forbidden combination and were removed. The design is no longer "
              "orthogonal or balanced; effects that were separate may now be partly confounded.",
        "es": "{n} de {total} ejecuciones previstas caían en una combinación prohibida y se quitaron. El diseño ya no "
              "es ortogonal ni equilibrado; efectos que estaban separados pueden quedar ahora parcialmente "
              "confundidos."},
    "ex.all_forbidden": {
        "en": "every planned run is forbidden", "es": "todas las ejecuciones previstas están prohibidas"},
    "ex.prop_note": {
        "en": "properties computed on the remaining runs",
        "es": "propiedades calculadas sobre las ejecuciones restantes"},
    "ex.label_main": {"en": "main effects", "es": "todos los efectos principales"},
    "ex.label_two": {"en": "two-factor interactions", "es": "todas las interacciones de dos factores"},
    "ex.warn_after_removal": {
        "en": "After removing runs, {label} can no longer all be estimated.",
        "es": "Tras quitar ejecuciones, ya no se pueden estimar {label}."},
    "ex.too_few_remain": {
        "en": "too few runs remain to estimate the main effects",
        "es": "quedan muy pocas ejecuciones para estimar los efectos principales"},
    "ex.design_note": {
        "en": "Run in the 'run' order (randomised to protect against drift); 'std_order' is the standard order. "
              "Center points estimate curvature and pure error.",
        "es": "Ejecuta en el orden «run» (aleatorizado para protegerse de la deriva); «std_order» es el orden "
              "estándar. Los puntos centrales estiman la curvatura y el error puro."},
    "ex.drop_aliased": {"en": "aliased with an earlier term", "es": "confundido con un término anterior"},
    "ex.drop_dependent": {
        "en": "linearly dependent on earlier terms", "es": "linealmente dependiente de términos anteriores"},
    "ex.drop_squares": {
        "en": "squares cannot be told apart with a third level only at the centre; see the curvature test",
        "es": "los cuadrados no se pueden distinguir con un tercer nivel solo en el centro; mira la prueba de "
              "curvatura"},
    "ex.too_few_runs": {
        "en": "{n} runs are too few for {k} factors: need at least {need}",
        "es": "{n} ejecuciones son pocas para {k} factores: se necesitan al menos {need}"},
    "ex.model_kind": {
        "en": "model must be 'auto', 'main', 'interactions' or 'quadratic'",
        "es": "model debe ser «auto», «main», «interactions» o «quadratic»"},
    "ex.warn_lenth_few": {
        "en": "Only {dof} residual degree(s) of freedom and no replicates: significance is judged with Lenth's pseudo "
              "standard error (which assumes most effects are zero) rather than the classical t-test.",
        "es": "Solo hay {dof} grado(s) de libertad residuales y no hay réplicas: la significación se juzga con el "
              "pseudoerror estándar de Lenth (que supone que la mayoría de los efectos son cero) en lugar de la "
              "prueba t clásica."},
    "ex.warn_saturated_lenth": {
        "en": "The design is saturated (as many terms as distinct runs): there is no error estimate, so significance "
              "is judged with Lenth's pseudo standard error, which assumes most effects are zero.",
        "es": "El diseño está saturado (tantos términos como ejecuciones distintas): no hay estimación del error, así "
              "que la significación se juzga con el pseudoerror estándar de Lenth, que supone que la mayoría de los "
              "efectos son cero."},
    "ex.warn_saturated": {
        "en": "The design is saturated: there is no error estimate and too few effects for Lenth's method; effects "
              "are reported without intervals.",
        "es": "El diseño está saturado: no hay estimación del error y hay muy pocos efectos para el método de Lenth; "
              "los efectos se dan sin intervalos."},
    "ex.warn_dof": {
        "en": "Only {dof} residual degrees of freedom: the noise estimate, and every interval, is very uncertain.",
        "es": "Solo hay {dof} grados de libertad residuales: la estimación del ruido, y todos los intervalos, son muy "
              "inciertos."},
    "ex.warn_lof": {
        "en": "Lack of fit is significant (p={p:.3g}): the model form is too simple for this surface (curvature or "
              "missing interactions); the effects below are not the whole story.",
        "es": "La falta de ajuste es significativa (p={p:.3g}): la forma del modelo es demasiado simple para esta "
              "superficie (curvatura o interacciones ausentes); los efectos de abajo no cuentan toda la historia."},
    "ex.warn_curv": {
        "en": "Curvature detected (p={p:.3g}): the response at the centre differs from the factorial average, so a "
              "straight-line model is wrong inside the region. Add axial points (central composite) to fit the "
              "curvature.",
        "es": "Curvatura detectada (p={p:.3g}): la respuesta en el centro difiere del promedio factorial, así que un "
              "modelo lineal es incorrecto dentro de la región. Añade puntos axiales (central compuesto) para ajustar "
              "la curvatura."},
    "ex.note_center_untested": {
        "en": "Centre points exist but cannot be tested for curvature (too little replication).",
        "es": "Hay puntos centrales, pero no se puede contrastar la curvatura (hay muy pocas réplicas)."},
    "ex.note_no_center": {
        "en": "No centre points: curvature cannot be checked; the straight-line model may hide it.",
        "es": "No hay puntos centrales: no se puede comprobar la curvatura; el modelo lineal puede ocultarla."},
    "ex.note_aliased": {
        "en": "Some terms are completely aliased in this design and are shown together: {items}",
        "es": "Algunos términos están completamente confundidos en este diseño y se muestran juntos: {items}"},
    "ex.alias_item": {"en": "{a} with {b}", "es": "{a} con {b}"},
    "ex.note_dropped": {"en": "Not estimated: {items}", "es": "No estimados: {items}"},
    "ex.note_interval": {
        "en": "Interval widths assume that terms not in the model (higher-order interactions) are zero.",
        "es": "La anchura de los intervalos supone que los términos que no están en el modelo (interacciones de "
              "orden superior) son cero."},
    "ex.warn_normal": {
        "en": "Residuals look non-normal (Shapiro p={p:.3g}); the p-values and intervals are approximate.",
        "es": "Los residuos no parecen normales (Shapiro p={p:.3g}); los valores p y los intervalos son "
              "aproximados."},
    "ex.coding": {
        "en": "coded -1..+1 between each factor's low and high; an effect is the response change from low to high",
        "es": "codificado de -1 a +1 entre el mínimo y el máximo de cada factor; un efecto es el cambio de la "
              "respuesta de bajo a alto"},
    "ex.stationary_note": {
        "en": "The stationary point lies outside the studied region: it is an extrapolation, not a result.",
        "es": "El punto estacionario queda fuera de la región estudiada: es una extrapolación, no un resultado."},
    "ex.warn_stationary": {
        "en": "The stationary point of the fitted surface lies outside the studied region; do not read it as an "
              "optimum.",
        "es": "El punto estacionario de la superficie ajustada queda fuera de la región estudiada; no lo leas como un "
              "óptimo."},
    "ex.cand_all": {
        "en": "every candidate needs all factors {names}", "es": "cada candidato necesita todos los factores {names}"},
    "ex.cand_bounds": {
        "en": "a candidate lies outside the bounds of {name!r} [{lo}, {hi}]",
        "es": "un candidato queda fuera de los límites de {name!r} [{lo}, {hi}]"},
    "ex.cand_none": {
        "en": "no candidate point satisfies the constraints", "es": "ningún punto candidato cumple las restricciones"},
    "ex.objective": {
        "en": "objective must be 'explore', 'maximize' or 'minimize'",
        "es": "objective debe ser «explore», «maximize» o «minimize»"},
    "ex.n_range": {"en": "n must be between 1 and 20", "es": "n debe estar entre 1 y 20"},
    "ex.warn_relative": {
        "en": "The model uses up every degree of freedom, so the noise level is unknown: standard deviations are in "
              "units of the (unknown) noise sd and expected improvement cannot be computed in response units.",
        "es": "El modelo agota todos los grados de libertad, así que se desconoce el nivel de ruido: las desviaciones "
              "estándar están en unidades de la desviación del ruido (desconocida) y la mejora esperada no se puede "
              "calcular en unidades de la respuesta."},
    "ex.warn_shaky": {
        "en": "Only {dof} residual degrees of freedom: the noise estimate is shaky, so the uncertainty ranking is "
              "rough. A replicate at the centre would help the noise estimate.",
        "es": "Solo hay {dof} grados de libertad residuales: la estimación del ruido es endeble, así que la "
              "ordenación por incertidumbre es aproximada. Una réplica en el centro ayudaría a estimar el ruido."},
    "ex.warn_no_ei": {
        "en": "No candidate has a meaningful expected improvement under the fitted model; recommending the most "
              "uncertain point instead.",
        "es": "Ningún candidato tiene una mejora esperada significativa con el modelo ajustado; se recomienda en su "
              "lugar el punto más incierto."},
    "ex.units_suffix": {"en": " in response units", "es": " en unidades de la respuesta"},
    "ex.why_explore": {
        "en": "largest remaining prediction uncertainty among {n} feasible candidates (sd {sd:.4g}{units}, versus "
              "{median:.4g} for the median candidate and {centre:.4g} at the centre)",
        "es": "mayor incertidumbre de predicción restante entre {n} candidatos viables (desviación {sd:.4g}{units}, "
              "frente a {median:.4g} del candidato mediano y {centre:.4g} en el centro)"},
    "ex.why_best": {
        "en": "best expected improvement for {objective} ({ei:.4g}): predicted {mean:.4g} against the best observed "
              "{best:.4g}, with prediction sd {sd:.4g}",
        "es": "mejor mejora esperada para {objective} ({ei:.4g}): predicho {mean:.4g} frente al mejor observado "
              "{best:.4g}, con desviación de predicción {sd:.4g}"},
    "ex.why_repeat": {
        "en": "it repeats an existing setting (in a balanced design every corner is equally uncertain), so it also "
              "sharpens the noise estimate",
        "es": "repite un ajuste existente (en un diseño equilibrado todas las esquinas son igual de inciertas), así "
              "que también afina la estimación del ruido"},
    "ex.why_distance": {
        "en": "{d:.2f} coded units from the nearest planned or existing run",
        "es": "a {d:.2f} unidades codificadas de la ejecución prevista o existente más cercana"},
    "ex.why_lower": {
        "en": "measuring it would lower the largest prediction sd from {before:.4g} to {after:.4g}",
        "es": "medirlo reduciría la mayor desviación de predicción de {before:.4g} a {after:.4g}"},
    "ex.why_outside": {
        "en": " It lies outside the coded -1..+1 region of the existing runs.",
        "es": " Queda fuera de la región codificada -1..+1 de las ejecuciones existentes."},
    "ex.next_caveat": {
        "en": "Both rules assume the fitted model form is right: a point in a region the model thinks is flat but is "
              "not can be missed. The variance rule is independent of the measured responses; only the improvement "
              "rule uses them.",
        "es": "Ambas reglas suponen que la forma del modelo ajustado es correcta: puede pasarse por alto un punto en "
              "una región que el modelo cree plana y no lo es. La regla de la varianza es independiente de las "
              "respuestas medidas; solo la regla de la mejora las usa."},
    "ex.test_outcomes": {
        "en": "test {test!r}: 'outcomes' maps each hypothesis to its predicted outcome(s)",
        "es": "prueba {test!r}: «outcomes» asigna a cada hipótesis su(s) resultado(s) previsto(s)"},
    "ex.test_missing": {
        "en": "test {test!r} gives no predicted outcome for hypothesis {h!r}",
        "es": "la prueba {test!r} no da ningún resultado previsto para la hipótesis {h!r}"},
    "ex.test_label": {
        "en": "test {test!r}, hypothesis {h!r}: give an outcome label or {{label: probability}}",
        "es": "prueba {test!r}, hipótesis {h!r}: indica una etiqueta de resultado o {{etiqueta: probabilidad}}"},
    "ex.test_probs": {
        "en": "test {test!r}, hypothesis {h!r}: outcome probabilities must be >= 0 and sum to 1 (got {tot:g})",
        "es": "prueba {test!r}, hipótesis {h!r}: las probabilidades de los resultados deben ser >= 0 y sumar 1 "
              "(suman {tot:g})"},
    "ex.two_hyp": {"en": "give at least two hypotheses", "es": "indica al menos dos hipótesis"},
    "ex.one_test": {"en": "give at least one test", "es": "indica al menos una prueba"},
    "ex.hyp_unique": {
        "en": "hypothesis names must be unique", "es": "los nombres de las hipótesis deben ser únicos"},
    "ex.prior_all": {
        "en": "give a prior for every hypothesis or for none",
        "es": "indica una probabilidad previa para todas las hipótesis o para ninguna"},
    "ex.prior_nonneg": {
        "en": "priors must be non-negative and not all zero",
        "es": "las probabilidades previas no pueden ser negativas ni todas cero"},
    "ex.test_repeated": {"en": "test name {name!r} is repeated", "es": "el nombre de prueba {name!r} está repetido"},
    "ex.test_cost": {"en": "test {name!r}: cost must be positive", "es": "prueba {name!r}: el coste debe ser positivo"},
    "ex.no_discrim": {
        "en": "none of the tests discriminates between the hypotheses: every hypothesis predicts the same outcomes",
        "es": "ninguna prueba discrimina entre las hipótesis: todas predicen los mismos resultados"},
    "ex.choose_reason": {
        "en": "{test} has the best information gain per unit cost: {gain:.3f} bits (of {h0:.3f} bits of uncertainty) "
              "for cost {cost:g}, i.e. {gpc:.3f} bits per unit cost.",
        "es": "{test} tiene la mejor ganancia de información por unidad de coste: {gain:.3f} bits (de {h0:.3f} bits "
              "de incertidumbre) por un coste de {cost:g}, es decir, {gpc:.3f} bits por unidad de coste."},
    "ex.choose_single": {
        "en": " Each of its possible outcomes points to a single hypothesis, so one run of this test settles the "
              "question.",
        "es": " Cada uno de sus posibles resultados apunta a una sola hipótesis, así que una ejecución de esta prueba "
              "zanja la cuestión."},
    "ex.choose_partial": {
        "en": " It does not separate every hypothesis by itself; the cheapest test that does is {test} (cost "
              "{cost:g}).",
        "es": " Por sí sola no separa todas las hipótesis; la prueba más barata que lo consigue es {test} (coste "
              "{cost:g})."},
    "ex.choose_none": {
        "en": " No single test separates all hypotheses; the sequential plan below combines several.",
        "es": " Ninguna prueba separa por sí sola todas las hipótesis; el plan secuencial de abajo combina varias."},
    "ex.assumptions": {
        "en": "The predicted outcome probabilities per hypothesis, the priors and the costs are your inputs; the "
              "ranking is only as good as they are.",
        "es": "Las probabilidades de resultado previstas por hipótesis, las probabilidades previas y los costes son "
              "datos tuyos; la ordenación solo es tan buena como ellos."},
    "ex.no_numeric": {
        "en": "column {name!r} has no numeric values", "es": "la columna {name!r} no tiene valores numéricos"},
    "ex.warn_ignored": {
        "en": "{n} rows with a missing factor or response were ignored.",
        "es": "Se ignoraron {n} filas con un factor o una respuesta ausentes."},
    "ex.warn_inferred": {
        "en": "Factor ranges were taken from the data (min and max): coded effects refer to that span. Give low and "
              "high to use the planned levels.",
        "es": "Los rangos de los factores se tomaron de los datos (mínimo y máximo): los efectos codificados se "
              "refieren a ese tramo. Indica low y high para usar los niveles previstos."},
})

register({
    "ex.drop_levels": {
        "en": "needs three or more levels of that factor", "es": "necesita tres o más niveles de ese factor"},
    "ex.drop_dof": {
        "en": "no degrees of freedom left (as many terms as distinct settings)",
        "es": "no quedan grados de libertad (tantos términos como ajustes distintos)"},
    "ex.space_filling": {
        "en": "maximin Latin hypercube (best of several seeded draws)",
        "es": "hipercubo latino maximin (el mejor de varios sorteos con semilla)"},
})


# ======================================================================= effect of a change (ef.*)
register({
    "ef.causal": {
        "en": "This is an estimate of the effect of the change under the assumptions listed below. The data alone "
              "cannot establish causation: the untestable assumptions have to be argued from knowledge of how the "
              "data arose.",
        "es": "Esto es una estimación del efecto del cambio bajo los supuestos que se listan abajo. Los datos por sí "
              "solos no pueden establecer causalidad: los supuestos que no se pueden contrastar hay que defenderlos "
              "con el conocimiento de cómo surgieron los datos."},
    "ef.failed_prefix": {
        "en": "One or more testable assumptions failed ({names}): do not read the estimate as the effect of the "
              "change. ",
        "es": "Han fallado uno o más supuestos contrastables ({names}): no leas la estimación como el efecto del "
              "cambio. "},
    "ef.bad_value": {
        "en": "cannot read {value!r} as a number or a date (use e.g. 2026-03-01)",
        "es": "no se puede leer {value!r} como número ni como fecha (usa, por ejemplo, 2026-03-01)"},
    "ef.say_treated": {
        "en": "say which value of the group column marks the treated group (treated_value); values found: {values}",
        "es": "indica qué valor de la columna de grupo marca el grupo tratado (treated_value); valores encontrados: "
              "{values}"},
    "ef.treated_selects": {
        "en": "treated_value {value!r} selects {which} row; values found: {values}",
        "es": "treated_value {value!r} selecciona {which} fila; valores encontrados: {values}"},
    "ef.which_every": {"en": "every", "es": "todas las"},
    "ef.which_no": {"en": "no", "es": "ninguna"},
    "ef.empty_cell": {
        "en": "the cell {cell} is empty: difference-in-differences needs treated and control rows both before and "
              "after {t0:g}",
        "es": "la celda {cell} está vacía: la diferencia en diferencias necesita filas tratadas y de control tanto "
              "antes como después de {t0:g}"},
    "ef.cell_treated_pre": {"en": "treated pre", "es": "tratado antes"},
    "ef.cell_treated_post": {"en": "treated post", "es": "tratado después"},
    "ef.cell_control_pre": {"en": "control pre", "es": "control antes"},
    "ef.cell_control_post": {"en": "control post", "es": "control después"},
    "ef.warn_missing": {
        "en": "{n} rows with missing values were ignored.", "es": "Se ignoraron {n} filas con valores ausentes."},
    "ef.warn_cell5": {
        "en": "A cell has fewer than 5 rows ({cells}): the estimate and its interval are fragile.",
        "es": "Una celda tiene menos de 5 filas ({cells}): la estimación y su intervalo son frágiles."},
    "ef.warn_clusters": {
        "en": "Only {n} clusters: cluster-robust intervals are approximate with so few units.",
        "es": "Solo hay {n} conglomerados: los intervalos robustos por conglomerado son aproximados con tan pocas "
              "unidades."},
    "ef.warn_no_unit": {
        "en": "No unit column: rows are treated as independent (robust to unequal variance only). With repeated "
              "measures of the same units this understates the uncertainty; give a unit column for cluster-robust "
              "errors.",
        "es": "Sin columna de unidad: las filas se tratan como independientes (solo robusto a varianza desigual). Con "
              "medidas repetidas de las mismas unidades esto subestima la incertidumbre; indica una columna de unidad "
              "para tener errores robustos por conglomerado."},
    # assumptions: name / statement / detail
    "ef.a_parallel": {"en": "parallel pre-trends", "es": "tendencias previas paralelas"},
    "ef.s_parallel": {
        "en": "Without the change, treated and control groups would have moved in parallel.",
        "es": "Sin el cambio, los grupos tratado y de control se habrían movido en paralelo."},
    "ef.d_parallel": {
        "en": "differential pre-period slope {est:.4g} per period (95% CI {lo:.4g} to {hi:.4g}, p={p:.3g}); if that "
              "drift continued it would add about {bias:.4g} to the estimate.",
        "es": "pendiente diferencial previa de {est:.4g} por periodo (IC del 95 % de {lo:.4g} a {hi:.4g}, p={p:.3g}); "
              "si esa deriva continuara, sumaría unos {bias:.4g} a la estimación."},
    "ef.d_parallel_tail": {
        "en": " Not rejecting parallel trends does not prove them, especially with few pre-periods.",
        "es": " No rechazar el paralelismo no lo demuestra, sobre todo con pocos periodos previos."},
    "ef.warn_parallel": {
        "en": "Parallel pre-trends rejected (p={p:.3g}): the groups were already diverging before the change, so the "
              "difference-in-differences estimate mixes the effect with that drift (about {bias:.4g} if it "
              "continued).",
        "es": "Tendencias previas paralelas rechazadas (p={p:.3g}): los grupos ya divergían antes del cambio, así que "
              "la estimación de diferencia en diferencias mezcla el efecto con esa deriva (unos {bias:.4g} si "
              "continuara)."},
    "ef.d_parallel_nt": {
        "en": "needs at least 3 pre-change time points with both groups; found {n}.",
        "es": "necesita al menos 3 instantes previos al cambio con ambos grupos; hay {n}."},
    "ef.warn_parallel_nt": {
        "en": "Parallel trends cannot be tested with fewer than 3 pre-change time points; the estimate relies on it "
              "unchecked.",
        "es": "No se puede contrastar el paralelismo con menos de 3 instantes previos al cambio; la estimación se "
              "apoya en él sin comprobarlo."},
    "ef.a_placebo_date": {"en": "no effect at a placebo date", "es": "sin efecto en una fecha placebo"},
    "ef.s_placebo_date": {
        "en": "Using only pre-change data and a fake change date, the estimated 'effect' should be zero.",
        "es": "Usando solo datos previos al cambio y una fecha de cambio falsa, el «efecto» estimado debería ser "
              "cero."},
    "ef.s_placebo_date_nt": {
        "en": "A fake change date inside the pre-period should show no effect.",
        "es": "Una fecha de cambio falsa dentro del periodo previo no debería mostrar efecto."},
    "ef.d_placebo_date": {
        "en": "placebo estimate {est:.4g} (95% CI {lo:.4g} to {hi:.4g}, p={p:.3g}).",
        "es": "estimación placebo de {est:.4g} (IC del 95 % de {lo:.4g} a {hi:.4g}, p={p:.3g})."},
    "ef.warn_placebo_date": {
        "en": "A placebo change at {pt:g} shows an 'effect' of {est:.4g} (p={p:.3g}): the design finds effects where "
              "none was applied.",
        "es": "Un cambio placebo en {pt:g} muestra un «efecto» de {est:.4g} (p={p:.3g}): el diseño encuentra efectos "
              "donde no se aplicó ninguno."},
    "ef.d_placebo_date_nt": {
        "en": "the pre-period has fewer than 4 distinct time points on each side.",
        "es": "el periodo previo tiene menos de 4 instantes distintos a cada lado."},
    "ef.warn_resid_ac": {
        "en": "Residuals are positively autocorrelated within groups (lag-1 {r:.2f}) and no unit column was given: "
              "the interval is too narrow.",
        "es": "Los residuos están autocorrelados positivamente dentro de los grupos (retardo 1: {r:.2f}) y no se "
              "indicó columna de unidad: el intervalo es demasiado estrecho."},
    "ef.a_other_change": {"en": "no other change at the same time", "es": "ningún otro cambio al mismo tiempo"},
    "ef.s_other_change": {
        "en": "Nothing else that affects the outcome changed for one group at the change date.",
        "es": "No cambió nada más que afecte al resultado en un grupo en la fecha del cambio."},
    "ef.s_other_change_its": {
        "en": "Nothing else that affects the series changed at the change date.",
        "es": "No cambió nada más que afecte a la serie en la fecha del cambio."},
    "ef.d_untestable": {
        "en": "cannot be tested from these data.", "es": "no se puede contrastar con estos datos."},
    "ef.a_spillover": {"en": "no spillover between groups", "es": "sin contagio entre grupos"},
    "ef.s_spillover": {
        "en": "The change did not affect the control group.", "es": "El cambio no afectó al grupo de control."},
    "ef.a_composition": {"en": "stable group composition", "es": "composición de grupos estable"},
    "ef.s_composition": {
        "en": "The units in each group are comparable before and after.",
        "es": "Las unidades de cada grupo son comparables antes y después."},
    "ef.season": {"en": "season {r}/{period}", "es": "estación {r}/{period}"},
    "ef.ar1_model": {
        "en": "AR(1) errors by Prais-Winsten (rho {rho:.2f}); residuals were autocorrelated (Ljung-Box p={p:.3g})",
        "es": "errores AR(1) por Prais-Winsten (rho {rho:.2f}); los residuos estaban autocorrelados (Ljung-Box "
              "p={p:.3g})"},
    "ef.warn_same_time": {
        "en": "Several rows share a timestamp: they were averaged into one value per time.",
        "es": "Varias filas comparten marca de tiempo: se promediaron en un único valor por instante."},
    "ef.its_points": {
        "en": "an interrupted time series needs at least 5 points before and 2 after the change (found {npre} and "
              "{npost})",
        "es": "una serie temporal interrumpida necesita al menos 5 puntos antes y 2 después del cambio (hay {npre} y "
              "{npost})"},
    "ef.warn_pre_points": {
        "en": "Only {n} points before the change: the pre-trend, and so the counterfactual, is poorly determined.",
        "es": "Solo hay {n} puntos antes del cambio: la tendencia previa, y con ella el contrafactual, está mal "
              "determinada."},
    "ef.warn_post_points": {
        "en": "Only {n} points after the change: the slope change cannot be judged.",
        "es": "Solo hay {n} puntos después del cambio: no se puede juzgar el cambio de pendiente."},
    "ef.season_points": {
        "en": "season_period {period} needs at least {need} points before the change; found {npre}",
        "es": "season_period {period} necesita al menos {need} puntos antes del cambio; hay {npre}"},
    "ef.warn_ar": {
        "en": "The series is autocorrelated (lag-1 {r:.2f}); intervals use {model}. With a short series they remain "
              "approximate and tend to be a little too narrow.",
        "es": "La serie está autocorrelada (retardo 1: {r:.2f}); los intervalos usan {model}. Con una serie corta "
              "siguen siendo aproximados y tienden a ser algo demasiado estrechos."},
    "ef.a_linear_pre": {"en": "linear pre-trend continues", "es": "la tendencia lineal previa continúa"},
    "ef.s_linear_pre": {
        "en": "Without the change the series would have kept its straight-line pre-trend.",
        "es": "Sin el cambio, la serie habría mantenido su tendencia previa en línea recta."},
    "ef.d_linear_pre": {
        "en": "curvature in the pre-period p={p:.3g}.", "es": "curvatura en el periodo previo p={p:.3g}."},
    "ef.d_linear_bad": {
        "en": " The straight-line counterfactual is questionable.",
        "es": " El contrafactual en línea recta es cuestionable."},
    "ef.d_linear_ok": {
        "en": " (This cannot show the trend would have continued after the change.)",
        "es": " (Esto no puede demostrar que la tendencia habría continuado tras el cambio.)"},
    "ef.warn_curved": {
        "en": "The pre-period is curved: projecting its straight line forward misstates the counterfactual.",
        "es": "El periodo previo es curvo: proyectar su línea recta hacia delante deforma el contrafactual."},
    "ef.d_linear_nt": {"en": "fewer than 8 pre-change points.", "es": "menos de 8 puntos previos al cambio."},
    "ef.a_placebo_levels": {"en": "no level change at placebo dates", "es": "sin cambio de nivel en fechas placebo"},
    "ef.s_placebo_levels": {
        "en": "Using only pre-change data and fake change dates, the estimated level change should be zero.",
        "es": "Usando solo datos previos al cambio y fechas de cambio falsas, el cambio de nivel estimado debería "
              "ser cero."},
    "ef.s_placebo_levels_nt": {
        "en": "Fake change dates inside the pre-period should show no effect.",
        "es": "Las fechas de cambio falsas dentro del periodo previo no deberían mostrar efecto."},
    "ef.d_placebo_levels": {
        "en": "{rej} of {n} placebo dates show a significant level change (Bonferroni-adjusted for {n} dates).",
        "es": "{rej} de {n} fechas placebo muestran un cambio de nivel significativo (ajuste de Bonferroni para {n} "
              "fechas)."},
    "ef.warn_placebo_levels": {
        "en": "{rej} of {n} placebo dates show a 'level change': the series jumps without any change being applied, "
              "so the real estimate may be noise or a structural break.",
        "es": "{rej} de {n} fechas placebo muestran un «cambio de nivel»: la serie salta sin que se haya aplicado "
              "ningún cambio, así que la estimación real puede ser ruido o una ruptura estructural."},
    "ef.d_placebo_levels_nt": {"en": "fewer than 10 pre-change points.", "es": "menos de 10 puntos previos al cambio."},
    "ef.a_measurement": {"en": "stable measurement", "es": "medición estable"},
    "ef.s_measurement": {
        "en": "How the series is measured did not change at the change date.",
        "es": "La forma de medir la serie no cambió en la fecha del cambio."},
    "ef.its_definition": {
        "en": "immediate level change at the intervention, relative to the projected pre-trend",
        "es": "cambio de nivel inmediato en la intervención, respecto a la tendencia previa proyectada"},
    "ef.dummies": {
        "en": "{name}: text column coded as {n} dummy variables (reference {ref!r}).",
        "es": "{name}: columna de texto codificada como {n} variables ficticias (referencia {ref!r})."},
    "ef.too_many_levels": {
        "en": "confounder {name!r} has {n} distinct text values; group them first (at most 12 levels)",
        "es": "el factor de confusión {name!r} tiene {n} valores de texto distintos; agrúpalos antes (como máximo 12 "
              "niveles)"},
    "ef.too_few_rows": {
        "en": "{n} rows are too few for {p} confounders and a treatment",
        "es": "{n} filas son pocas para {p} factores de confusión y un tratamiento"},
    "ef.warn_small_group": {
        "en": "Only {n} rows in the smaller group: estimates are fragile.",
        "es": "Solo hay {n} filas en el grupo más pequeño: las estimaciones son frágiles."},
    "ef.no_variation": {"en": "the treatment does not vary", "es": "el tratamiento no varía"},
    "ef.warn_collinear": {
        "en": "Some confounders are perfectly collinear with others or with the treatment; their coefficients are not "
              "identified.",
        "es": "Algunos factores de confusión son perfectamente colineales con otros o con el tratamiento; sus "
              "coeficientes no están identificados."},
    "ef.adjust_def_binary": {
        "en": "difference in the mean outcome between treated and untreated at the same confounder values (linear "
              "adjustment)",
        "es": "diferencia en el resultado medio entre tratados y no tratados con los mismos valores de los factores "
              "de confusión (ajuste lineal)"},
    "ef.adjust_def_cont": {
        "en": "change in outcome per unit of the treatment variable, holding the confounders fixed",
        "es": "cambio en el resultado por unidad de la variable de tratamiento, manteniendo fijos los factores de "
              "confusión"},
    "ef.warn_outside": {
        "en": "{share:.0%} of treated rows lie outside the control range of {name}: the adjustment extrapolates "
              "there.",
        "es": "El {share:.0%} de las filas tratadas queda fuera del rango de control de {name}: el ajuste "
              "extrapola ahí."},
    "ef.warn_imbalance": {
        "en": "Large imbalance (|standardized difference| > 0.25) in {names}: linear adjustment relies heavily on the "
              "model form.",
        "es": "Gran desequilibrio (|diferencia estandarizada| > 0,25) en {names}: el ajuste lineal depende mucho de "
              "la forma del modelo."},
    "ef.warn_overlap": {
        "en": "Weak overlap: {share:.0%} of rows lie outside the propensity range shared by both groups (range "
              "{lo:.2f} to {hi:.2f}). Some treated units have no comparable untreated unit.",
        "es": "Solapamiento débil: el {share:.0%} de las filas queda fuera del rango de propensión que comparten "
              "ambos grupos (rango de {lo:.2f} a {hi:.2f}). Algunas unidades tratadas no tienen una unidad no tratada "
              "comparable."},
    "ef.reading": {
        "en": "An unmeasured confounder would need to explain at least {rv:.1%} of the remaining variance of both the "
              "treatment and the outcome to reduce the estimate to zero",
        "es": "Un factor de confusión no medido tendría que explicar al menos el {rv:.1%} de la varianza restante "
              "tanto del tratamiento como del resultado para reducir la estimación a cero"},
    "ef.reading_ci": {
        "en": " ({rv:.1%} to make the interval include zero).",
        "es": " (el {rv:.1%} para que el intervalo incluya el cero)."},
    "ef.warn_fragile": {
        "en": "The estimate is fragile: a weak unmeasured confounder (robustness value {rv:.1%}) could remove it.",
        "es": "La estimación es frágil: un factor de confusión no medido y débil (valor de robustez {rv:.1%}) podría "
              "eliminarla."},
    "ef.a_placebo_outcome": {"en": "no effect on a placebo outcome", "es": "sin efecto en un resultado placebo"},
    "ef.s_placebo_outcome": {
        "en": "An outcome measured before the change, or one the change cannot affect, should show no 'effect'.",
        "es": "Un resultado medido antes del cambio, o uno que el cambio no puede afectar, no debería mostrar "
              "«efecto»."},
    "ef.s_placebo_outcome_nt": {
        "en": "An outcome the change cannot affect should show no 'effect'.",
        "es": "Un resultado que el cambio no puede afectar no debería mostrar «efecto»."},
    "ef.d_placebo_outcome": {
        "en": "placebo estimate {est:.4g} (95% CI {lo:.4g} to {hi:.4g}).",
        "es": "estimación placebo de {est:.4g} (IC del 95 % de {lo:.4g} a {hi:.4g})."},
    "ef.warn_placebo_outcome": {
        "en": "The placebo outcome shows an 'effect': the adjusted comparison is picking up differences the listed "
              "confounders do not remove.",
        "es": "El resultado placebo muestra un «efecto»: la comparación ajustada capta diferencias que los factores "
              "de confusión indicados no eliminan."},
    "ef.d_placebo_outcome_nt": {
        "en": "no placebo outcome was given.", "es": "no se indicó ningún resultado placebo."},
    "ef.a_overlap": {
        "en": "overlap (comparable units in both groups)", "es": "solapamiento (unidades comparables en ambos grupos)"},
    "ef.s_overlap": {
        "en": "For every kind of unit there are both treated and untreated cases.",
        "es": "Para cada tipo de unidad hay casos tratados y no tratados."},
    "ef.d_overlap": {
        "en": "{share:.0%} of rows outside the shared propensity range.",
        "es": "el {share:.0%} de las filas queda fuera del rango de propensión compartido."},
    "ef.d_overlap_nc": {"en": "propensity overlap not computed.", "es": "no se calculó el solapamiento de propensión."},
    "ef.d_imbalanced": {
        "en": " Imbalanced before adjustment: {names}.", "es": " Desequilibrados antes del ajuste: {names}."},
    "ef.a_unmeasured": {"en": "no unmeasured confounding", "es": "sin factores de confusión no medidos"},
    "ef.s_unmeasured": {
        "en": "The listed confounders are all the common causes of treatment and outcome.",
        "es": "Los factores de confusión indicados son todas las causas comunes del tratamiento y del resultado."},
    "ef.d_unmeasured": {
        "en": "cannot be tested; see the robustness value for how strong a missing confounder would have to be.",
        "es": "no se puede contrastar; mira el valor de robustez para saber lo fuerte que tendría que ser un factor "
              "de confusión ausente."},
    "ef.a_form": {"en": "correct functional form", "es": "forma funcional correcta"},
    "ef.s_form": {
        "en": "The outcome is linear in the confounders.",
        "es": "El resultado es lineal en los factores de confusión."},
    "ef.d_form": {
        "en": "check the stability table; large shifts signal sensitivity.",
        "es": "revisa la tabla de estabilidad; los cambios grandes indican sensibilidad."},
    "ef.warn_no_conf": {
        "en": "No confounders were listed: the 'adjusted' estimate is just the raw difference and is very likely "
              "confounded.",
        "es": "No se indicó ningún factor de confusión: la estimación «ajustada» es solo la diferencia bruta y muy "
              "probablemente está confundida."},
    "ef.need_did": {
        "en": "difference-in-differences needs {need}", "es": "la diferencia en diferencias necesita {need}"},
    "ef.need_its": {
        "en": "an interrupted time series needs {need}", "es": "una serie temporal interrumpida necesita {need}"},
    "ef.need_treatment": {
        "en": "regression adjustment needs the treatment column",
        "es": "el ajuste por regresión necesita la columna de tratamiento"},
    "ef.method": {
        "en": "method must be 'did', 'its' or 'adjust'", "es": "method debe ser «did», «its» o «adjust»"},
})

register({
    "ef.term_pretrend": {"en": "pre-trend (per step)", "es": "tendencia previa (por paso)"},
    "ef.term_level": {"en": "level change", "es": "cambio de nivel"},
    "ef.term_slope": {"en": "slope change (per step)", "es": "cambio de pendiente (por paso)"},
})


# ======================================================================= data migration (mg.*)
register({
    "mg.name": {
        "en": "a migration name uses letters, digits, '_' or '-' (1 to 60 characters)",
        "es": "el nombre de una migración usa letras, dígitos, «_» o «-» (de 1 a 60 caracteres)"},
    "mg.spec_object": {"en": "the spec must be a JSON object", "es": "la especificación debe ser un objeto JSON"},
    "mg.need_table": {
        "en": "the spec needs at least one table", "es": "la especificación necesita al menos una tabla"},
    "mg.table_name": {"en": "every table needs a name", "es": "cada tabla necesita un nombre"},
    "mg.table_twice": {"en": "table {t!r} is listed twice", "es": "la tabla {t!r} aparece dos veces"},
    "mg.table_source": {
        "en": "table {t!r}: give exactly one of 'source' (dataset name) or 'source_sql'",
        "es": "tabla {t!r}: indica exactamente uno de «source» (nombre del conjunto de datos) o «source_sql»"},
    "mg.table_columns": {"en": "table {t!r} needs columns", "es": "la tabla {t!r} necesita columnas"},
    "mg.col_name": {"en": "table {t!r}: every column needs a name", "es": "tabla {t!r}: cada columna necesita un nombre"},
    "mg.col_twice": {
        "en": "table {t!r}: column {c!r} is listed twice", "es": "tabla {t!r}: la columna {c!r} aparece dos veces"},
    "mg.col_type": {
        "en": "table {t!r}, column {c!r}: type must be one of {types}",
        "es": "tabla {t!r}, columna {c!r}: el tipo debe ser uno de {types}"},
    "mg.col_kind": {
        "en": "table {t!r}, column {c!r}: kind must be 'representation' or 'correction'",
        "es": "tabla {t!r}, columna {c!r}: kind debe ser «representation» o «correction»"},
    "mg.key_col": {
        "en": "table {t!r}: key column {c!r} is not a column of the table",
        "es": "tabla {t!r}: la columna de clave {c!r} no es una columna de la tabla"},
    "mg.fk_shape": {
        "en": "table {t!r}: a foreign key needs columns, ref_table and the same number of ref_columns",
        "es": "tabla {t!r}: una clave foránea necesita columns, ref_table y el mismo número de ref_columns"},
    "mg.fk_col": {
        "en": "table {t!r}: foreign key column {c!r} is not a column of the table",
        "es": "tabla {t!r}: la columna de clave foránea {c!r} no es una columna de la tabla"},
    "mg.fk_table": {
        "en": "table {t!r}: foreign key refers to unknown table {ref!r}",
        "es": "tabla {t!r}: la clave foránea apunta a una tabla desconocida {ref!r}"},
    "mg.fk_refcol": {
        "en": "table {t!r}: {ref!r} has no column {c!r}", "es": "tabla {t!r}: {ref!r} no tiene la columna {c!r}"},
    "mg.fk_refkey": {
        "en": "table {t!r}: ref_columns {cols} must be the primary key or a unique key of {ref!r}",
        "es": "tabla {t!r}: ref_columns {cols} debe ser la clave primaria o una clave única de {ref!r}"},
    "mg.total_table": {
        "en": "every total check needs 'table' naming a table of the spec",
        "es": "cada comprobación de totales necesita «table» con el nombre de una tabla de la especificación"},
    "mg.total_fields": {
        "en": "a total check needs source_expr and target_column",
        "es": "una comprobación de totales necesita source_expr y target_column"},
    "mg.total_col": {
        "en": "total check: {t!r} has no column {c!r}",
        "es": "comprobación de totales: {t!r} no tiene la columna {c!r}"},
    "mg.tolerance": {"en": "tolerance must be >= 0", "es": "la tolerancia debe ser >= 0"},
    "mg.func_fields": {
        "en": "a functional check needs name, source_sql and target_sql",
        "es": "una comprobación funcional necesita name, source_sql y target_sql"},
    "mg.fk_cycle": {
        "en": "foreign keys form a cycle among {left}; break it before rehearsing",
        "es": "las claves foráneas forman un ciclo entre {left}; rómpelo antes de ensayar"},
    "mg.src_nocol": {
        "en": "table {t!r}: the source has no column {c!r}; give an expr (source columns: {cols})",
        "es": "tabla {t!r}: el origen no tiene la columna {c!r}; indica una expr (columnas del origen: {cols})"},
    "mg.src_refcol": {
        "en": "table {t!r}, column {c!r}: source_column {ref!r} is not in the source",
        "es": "tabla {t!r}, columna {c!r}: source_column {ref!r} no está en el origen"},
    "mg.expr_failed": {
        "en": "table {t!r}: an expression failed on the source ({exc}). Use TRY_CAST or CASE in expr for values that "
              "cannot convert.",
        "es": "tabla {t!r}: una expresión falló sobre el origen ({exc}). Usa TRY_CAST o CASE en expr para los valores "
              "que no se pueden convertir."},
    "mg.cast_item": {
        "en": "{col}: {raw!r} is not a valid {type}", "es": "{col}: {raw!r} no es un valor {type} válido"},
    "mg.required_empty": {
        "en": "required column(s) empty: {cols}", "es": "columnas obligatorias vacías: {cols}"},
    "mg.dup_pk": {
        "en": "primary key {k} already used by an earlier row",
        "es": "la clave primaria {k} ya la usa una fila anterior"},
    "mg.dup_uq": {
        "en": "unique key {k} already used by an earlier row",
        "es": "la clave única {k} ya la usa una fila anterior"},
    "mg.fk_notfound": {
        "en": "{k} not found in {ref}({cols})", "es": "{k} no se encuentra en {ref}({cols})"},
    "mg.target_refused": {
        "en": "table {t!r}: the target refused a row ({exc})",
        "es": "tabla {t!r}: el destino rechazó una fila ({exc})"},
    "mg.note_no_ref": {
        "en": "no source column to compare against (name a source_column to enable the check)",
        "es": "no hay columna de origen con la que comparar (indica una source_column para activar la comprobación)"},
    "mg.note_correction_none": {
        "en": "declared correction; nothing was changed", "es": "corrección declarada; no se cambió nada"},
    "mg.note_correction": {
        "en": "{n} value(s) differ from the source column {ref!r} by design",
        "es": "{n} valor(es) difieren de la columna de origen {ref!r} por diseño"},
    "mg.note_repr_changes": {
        "en": "representation column changes {n} value(s): declare it a correction or fix the expression",
        "es": "la columna de representación cambia {n} valor(es): decláralo corrección o arregla la expresión"},
    "mg.note_roundtrip": {
        "en": "values survive the round trip", "es": "los valores sobreviven al viaje de ida y vuelta"},
    "mg.note_format_only": {
        "en": "; {n} are written differently (same value)", "es": "; {n} se escriben de otra forma (mismo valor)"},
    "mg.query_select": {
        "en": "target_sql must be a single SELECT", "es": "target_sql debe ser un único SELECT"},
    "mg.note_no_pk": {
        "en": "no primary key: a second load cannot be matched row by row, so idempotency is not proved",
        "es": "sin clave primaria: una segunda carga no se puede emparejar fila a fila, así que no se demuestra la "
              "idempotencia"},
    "mg.exp_read": {
        "en": "The source datasets were only read. The target is a new SQLite file rebuilt from scratch on every run.",
        "es": "Los conjuntos de datos de origen solo se leyeron. El destino es un archivo SQLite nuevo que se "
              "reconstruye desde cero en cada ejecución."},
    "mg.exp_order": {
        "en": "Tables are loaded parents first, so a foreign key can only point at a row that was actually loaded.",
        "es": "Las tablas se cargan empezando por las padre, así que una clave foránea solo puede apuntar a una fila "
              "que se cargó de verdad."},
    "mg.exp_every": {
        "en": "Every source row is either loaded or rejected with its reason: {source} source = {loaded} loaded + "
              "{rejected} rejected",
        "es": "Cada fila de origen se carga o se rechaza con su motivo: {source} de origen = {loaded} cargadas + "
              "{rejected} rechazadas"},
    "mg.exp_reconcile": {"en": " (counts reconcile).", "es": " (los recuentos cuadran)."},
    "mg.exp_no_reconcile": {"en": " (COUNTS DO NOT RECONCILE).", "es": " (LOS RECUENTOS NO CUADRAN)."},
    "mg.exp_reasons": {
        "en": "Reasons, in the order they are tested: a value that does not convert to the declared type, a required "
              "value missing, a repeated primary key, a repeated unique key, a foreign key without a parent. The "
              "earliest row keeps the key.",
        "es": "Motivos, en el orden en que se comprueban: un valor que no se convierte al tipo declarado, falta un "
              "valor obligatorio, una clave primaria repetida, una clave única repetida, una clave foránea sin padre. "
              "La fila más antigua conserva la clave."},
    "mg.exp_clean": {
        "en": "Text that is not a clean value of the target type is rejected, never rounded or guessed; use an "
              "expression (TRY_CAST, NULLIF, CASE) in the spec when you decide how it should convert.",
        "es": "El texto que no es un valor limpio del tipo de destino se rechaza, nunca se redondea ni se adivina; "
              "usa una expresión (TRY_CAST, NULLIF, CASE) en la especificación cuando decidas cómo debe convertirse."},
    "mg.exp_kinds": {
        "en": "A representation column may change only how a value is written; if its values differ from the source "
              "column it is flagged. A correction column is expected to change values and reports how many.",
        "es": "Una columna de representación solo puede cambiar cómo se escribe un valor; si sus valores difieren de "
              "la columna de origen se marca. Una columna de corrección está pensada para cambiar valores y dice "
              "cuántos."},
    "mg.exp_upsert": {
        "en": "The second load upserts by primary key into the same file; the report compares row counts and a "
              "content hash before and after.",
        "es": "La segunda carga hace upsert por clave primaria en el mismo archivo; el informe compara los recuentos "
              "de filas y un hash del contenido antes y después."},
    "mg.exp_rejected": {
        "en": "Rejected rows are listed with column and value; nothing rejected reaches the target.",
        "es": "Las filas rechazadas se listan con su columna y su valor; nada de lo rechazado llega al destino."},
    "mg.ev_native": {"en": "native type", "es": "tipo nativo"},
    "mg.ev_convert": {
        "en": "all {n} non-empty text values convert to {type}",
        "es": "los {n} valores de texto no vacíos se convierten a {type}"},
    "mg.ev_counts": {
        "en": "{evidence}; {nulls} missing, {distinct} distinct of {total}",
        "es": "{evidence}; {nulls} ausentes, {distinct} distintos de {total}"},
    "mg.give_spec": {
        "en": "give a saved spec name or an inline spec", "es": "indica el nombre de una especificación guardada o "
                                                              "una especificación en línea"},
    "mg.no_spec": {
        "en": "no saved migration spec named {name!r}", "es": "no hay ninguna especificación de migración guardada "
                                                              "llamada {name!r}"},
    "mg.suggest_note": {
        "en": "This is a suggestion drawn from the data, not a mapping. Check every type, required flag, key and kind "
              "before saving; nothing has been applied or saved.",
        "es": "Esto es una sugerencia sacada de los datos, no una asignación. Revisa cada tipo, indicador de "
              "obligatoriedad, clave y clase antes de guardar; no se ha aplicado ni guardado nada."},
})


# ======================================================================= reconciliation (rc.*)
register({
    "rc.issue": {
        "en": "{side} side: {missing} missing, {repeated} repeated",
        "es": "lado {side}: {missing} ausentes, {repeated} repetidos"},
    "rc.side_left": {"en": "left", "es": "izquierdo"},
    "rc.side_right": {"en": "right", "es": "derecho"},
    "rc.note_strong": {
        "en": "unique and complete on both sides; confirm before using it",
        "es": "única y completa en ambos lados; confírmala antes de usarla"},
    "rc.note_weak": {
        "en": "shares values but has missing or repeated keys: those rows will stay unpaired and listed",
        "es": "comparte valores pero tiene claves ausentes o repetidas: esas filas quedarán sin emparejar y "
              "listadas"},
    "rc.suggest_note": {
        "en": "Suggestions only. A shared value is evidence, not proof that two columns mean the same thing; no key is "
              "applied until you choose it.",
        "es": "Solo son sugerencias. Que compartan un valor es un indicio, no una prueba de que dos columnas "
              "signifiquen lo mismo; no se aplica ninguna clave hasta que la elijas."},
    "rc.need_key": {
        "en": "choose at least one key pair; keys are never guessed",
        "es": "elige al menos un par de claves; las claves nunca se adivinan"},
    "rc.tolerance": {"en": "tolerance must be >= 0", "es": "la tolerancia debe ser >= 0"},
    "rc.no_left": {
        "en": "left table has no column {c!r}", "es": "la tabla izquierda no tiene la columna {c!r}"},
    "rc.no_right": {
        "en": "right table has no column {c!r}", "es": "la tabla derecha no tiene la columna {c!r}"},
    "rc.cannot_compare": {
        "en": "cannot compare {a!r} with {b!r}: column missing",
        "es": "no se puede comparar {a!r} con {b!r}: falta una columna"},
    "rc.exp_key": {
        "en": "Key: left {left} = right {right}", "es": "Clave: izquierda {left} = derecha {right}"},
    "rc.exp_norm": {
        "en": ", normalised with {opts}", "es": ", normalizada con {opts}"},
    "rc.exp_exact": {
        "en": ", compared exactly as text", "es": ", comparada exactamente como texto"},
    "rc.exp_missing_key": {
        "en": ". A missing key component never matches.",
        "es": ". Un componente de clave ausente nunca coincide."},
    "rc.exp_ambiguous": {
        "en": "A key present more than once on either side is ambiguous: its rows are not paired with each other, so "
              "no duplicate is multiplied by the comparison.",
        "es": "Una clave que aparece más de una vez en cualquiera de los lados es ambigua: sus filas no se emparejan "
              "entre sí, así que ningún duplicado se multiplica en la comparación."},
    "rc.exp_kept": {
        "en": "Rows with a key on one side only are kept and listed; nothing is dropped.",
        "es": "Las filas con clave en un solo lado se conservan y se listan; no se descarta nada."},
    "rc.exp_cells": {
        "en": "Cell categories keep zero and missing apart (zero_vs_missing), and empty text apart from missing.",
        "es": "Las categorías de celda distinguen el cero del ausente (zero_vs_missing) y el texto vacío del ausente."},
    "rc.exp_tol": {
        "en": "Numbers within ±{tol} count as equal_within_tolerance, separately from exact equality.",
        "es": "Los números a ±{tol} cuentan como equal_within_tolerance, aparte de la igualdad exacta."},
})


# ======================================================================= workbook explain (wb.*)
register({
    "wb.delta": {
        "en": "delta must be a positive number (0.1 = ±10%)",
        "es": "delta debe ser un número positivo (0,1 = ±10 %)"},
    "wb.step_input": {
        "en": "{ref}: steps can only be given for input parameters",
        "es": "{ref}: los pasos solo se pueden indicar para parámetros de entrada"},
    "wb.step_positive": {
        "en": "{ref}: the step must be a positive number", "es": "{ref}: el paso debe ser un número positivo"},
    "wb.cell_empty": {"en": "{ref} is empty", "es": "{ref} está vacía"},
    "wb.skip_options": {"en": "chosen from a list of options", "es": "se elige de una lista de opciones"},
    "wb.kind_boolean": {"en": "boolean", "es": "lógico"},
    "wb.kind_empty": {"en": "empty", "es": "vacío"},
    "wb.kind_text": {"en": "text", "es": "texto"},
    "wb.kind_other": {"en": "not a number", "es": "no es un número"},
    "wb.skip_nonnumeric": {"en": "not numeric ({kind})", "es": "no numérico ({kind})"},
    "wb.skip_date": {
        "en": "date; give an absolute step in days to include it",
        "es": "fecha; indica un paso absoluto en días para incluirla"},
    "wb.skip_zero": {
        "en": "base value is 0; a relative change does nothing, give an absolute step",
        "es": "el valor base es 0; un cambio relativo no hace nada, indica un paso absoluto"},
    "wb.skip_more": {
        "en": "{n} more parameters beyond the first {max}", "es": "{n} parámetros más aparte de los {max} primeros"},
    "wb.no_rank": {
        "en": "the output is an error or not a number at the base values, so there is nothing to rank",
        "es": "la salida es un error o no es un número con los valores base, así que no hay nada que ordenar"},
    "wb.is_formula": {"en": "{addr} is a formula", "es": "{addr} es una fórmula"},
    "wb.unknown_unit": {"en": "unknown unit {text!r}: {detail}", "es": "unidad desconocida {text!r}: {detail}"},
    "wb.unit_number": {"en": "number", "es": "número"},
    "wb.cannot_combine": {
        "en": "cannot combine {a} and {b} with {op}", "es": "no se pueden combinar {a} y {b} con {op}"},
    "wb.branches": {
        "en": "the branches of {name} have different units: {a} and {b}",
        "es": "las ramas de {name} tienen unidades distintas: {a} y {b}"},
    "wb.mixes": {"en": "{name} mixes {a} and {b}", "es": "{name} mezcla {a} y {b}"},
    "wb.units_note": {
        "en": "Cells whose inputs have no unit stay unknown and never raise an error. A plain number in a formula takes "
              "the unit of what it is added to or compared with.",
        "es": "Las celdas cuyas entradas no tienen unidad quedan como desconocidas y nunca dan error. Un número suelto "
              "en una fórmula toma la unidad de aquello a lo que se suma o con lo que se compara."},
    "wb.scn_name": {
        "en": "give the scenario a name other than 'Base'", "es": "pon al escenario un nombre distinto de «Base»"},
    "wb.scn_inputs": {
        "en": "inputs are required: {{cell address or label: value}}",
        "es": "se necesitan entradas: {{dirección de celda o etiqueta: valor}}"},
    "wb.scn_unknown": {"en": "no scenario {name!r}", "es": "no existe el escenario {name!r}"},
    "wb.scn_none": {
        "en": "no scenarios saved yet; save one first", "es": "aún no hay escenarios guardados; guarda uno primero"},
    "wb.unit_formula": {
        "en": "{addr} is a formula: its unit is inferred, declare the units of the inputs",
        "es": "{addr} es una fórmula: su unidad se infiere; declara las unidades de las entradas"},
    "wb.scn_req_save": {
        "en": "scenario (the name) is required to save", "es": "scenario (el nombre) es obligatorio para guardar"},
    "wb.scn_req_delete": {
        "en": "scenario (the name) is required to delete", "es": "scenario (el nombre) es obligatorio para borrar"},
})

register({
    "ex.others_at": {"en": "centre of their range", "es": "centro de su rango"},
    "ef.ols_model": {
        "en": "ordinary least squares (no autocorrelation detected in the residuals)",
        "es": "mínimos cuadrados ordinarios (no se detecta autocorrelación en los residuos)"},
    "wb.no_compiled": {
        "en": "no compiled workbook {wid!r}", "es": "no hay ningún libro compilado {wid!r}"},
})

# ======================================================================= event log, profile and calibration (el.*)
register({
    "el.bad_zone": {
        "en": "timezone {zone!r} is not a known zone name (e.g. Europe/Madrid, UTC) or a fixed offset (+01:00)",
        "es": "la zona horaria {zone!r} no es un nombre de zona conocido (p. ej. Europe/Madrid, UTC) ni un desfase "
              "fijo (+01:00)"},
    "el.bad_list": {
        "en": "log_options.{key} must be a list of activity names",
        "es": "log_options.{key} debe ser una lista de nombres de actividad"},
    "el.bad_lifecycle_values": {
        "en": "log_options.lifecycle_values must be {{start: [...], complete: [...]}}",
        "es": "log_options.lifecycle_values debe ser {{start: [...], complete: [...]}}"},
    "el.start_and_lifecycle": {
        "en": "mapping: use either a start column (one row per step with start and end) or a lifecycle column (one "
              "row per start/complete event), not both",
        "es": "mapping: usa o bien una columna de inicio (una fila por paso con inicio y fin) o bien una columna de "
              "ciclo de vida (una fila por evento start/complete), no las dos"},
    "el.drop_lifecycle": {"en": "unknown lifecycle value", "es": "valor de ciclo de vida desconocido"},
    "el.ordering": {
        "en": "Raw events are ordered by timestamp, then lifecycle (start before complete), then source row; the "
              "steps of a case by completion time, then source row. Equal completion times are ties: their order "
              "is not inferred and the edges that depend on it are counted.",
        "es": "Los eventos se ordenan por marca de tiempo, después por ciclo de vida (inicio antes que fin) y después "
              "por fila de origen; los pasos de un caso, por momento de finalización y después por fila de origen. "
              "Los momentos de finalización iguales son empates: su orden no se deduce y se cuentan las aristas que "
              "dependen de él."},
    "el.zone_note": {
        "en": "Times without an offset were read as local time in {zone}; times with an offset keep it. Everything "
              "is shown in UTC.",
        "es": "Las horas sin desfase se han leído como hora local de {zone}; las que llevan desfase lo conservan. "
              "Todo se muestra en UTC."},
    "el.zone_note_ingest": {
        "en": "The timestamp column was stored with time zone at import: values with an offset are exact instants, "
              "values without one were read in the computer's zone at import (not the declared zone), and the "
              "written offsets are no longer in the data, so zone changes cannot be listed. To keep them, import the "
              "file again with the column as text (import option text_columns).",
        "es": "La columna de tiempo se guardó con zona horaria al importar: los valores con desfase son instantes "
              "exactos, los que no lo tenían se leyeron en la zona del equipo al importar (no en la zona declarada) "
              "y los desfases escritos ya no están en los datos, así que no pueden listarse los cambios de zona. Para "
              "conservarlos, vuelve a importar el archivo con la columna como texto (opción de importación "
              "text_columns)."},
    "el.no_closed": {
        "en": "no closed case to profile ({n} open); check the declared end activities",
        "es": "no hay ningún caso cerrado que perfilar ({n} abiertos); revisa las actividades finales declaradas"},
    "el.coverage_note": {
        "en": "Coverage: share of the observed transitions (and of the cases, end to end) that the kept edges "
              "explain.",
        "es": "Cobertura: proporción de las transiciones observadas (y de los casos completos) que explican las "
              "aristas conservadas."},
    "el.need_start": {
        "en": "{act}: {n} instances have no start time, so its waiting and its work cannot be separated; record a "
              "start event (or start column) for this step.",
        "es": "{act}: {n} ejecuciones no tienen hora de inicio, así que su espera y su trabajo no pueden separarse; "
              "registra un evento de inicio (o una columna de inicio) para este paso."},
    "el.need_start_all": {
        "en": "Missing data: start times. With only completion times, the time between two steps is elapsed time "
              "(waiting plus work, not separable) and capacity cannot be estimated.",
        "es": "Falta un dato: las horas de inicio. Con solo horas de finalización, el tiempo entre dos pasos es tiempo "
              "transcurrido (espera más trabajo, no separables) y la capacidad no puede estimarse."},
    "el.need_end": {
        "en": "Missing data: which activities close a case. Until they are declared no case can be called open; "
              "candidate (most frequent last activity, not applied): {act}.",
        "es": "Falta un dato: qué actividades cierran un caso. Mientras no se declaren, ningún caso puede "
              "considerarse abierto; candidata (última actividad más frecuente, no aplicada): {act}."},
    "el.need_capacity": {
        "en": "{act}: capacity is not identifiable without start times; record starts (and the resource) to "
              "estimate it.",
        "es": "{act}: la capacidad no es identificable sin horas de inicio; registra los inicios (y el recurso) para "
              "estimarla."},
    "el.explain_wait": {
        "en": "Work accumulates before {act}: {share:.0f}% of all waiting, median {median:.2f} h, p90 {p90:.2f} h "
              "(n = {n}); on average {avg:.2f} cases are waiting for it.",
        "es": "El trabajo se acumula antes de {act}: {share:.0f} % de toda la espera, mediana {median:.2f} h, "
              "p90 {p90:.2f} h (n = {n}); de media hay {avg:.2f} casos esperándolo."},
    "el.explain_elapsed": {
        "en": "Most elapsed time builds up before {act}: {share:.0f}% of it, median {median:.2f} h, p90 {p90:.2f} h "
              "(n = {n}). Without start times this mixes waiting and work: it is not proven to be waiting.",
        "es": "La mayor parte del tiempo transcurrido se acumula antes de {act}: {share:.0f} %, mediana "
              "{median:.2f} h, p90 {p90:.2f} h (n = {n}). Sin horas de inicio esto mezcla espera y trabajo: no está "
              "demostrado que sea espera."},
    "el.explain_concurrency": {
        "en": "{n} pairs of steps overlap in time inside a case (e.g. {a} and {b} in {cases} cases): they are "
              "concurrent there, so their order in the graph is only the order of completion.",
        "es": "{n} pares de pasos se solapan en el tiempo dentro de un caso (p. ej. {a} y {b} en {cases} casos): ahí "
              "son concurrentes, así que su orden en el grafo es solo el orden de finalización."},
    "el.times_sep": {
        "en": "Service time = end − start of each step; waiting = start − the latest completion before it in the "
              "case. Steps that start while another is running are counted as started in parallel, not as waiting.",
        "es": "Tiempo de servicio = fin − inicio de cada paso; espera = inicio − la última finalización anterior del "
              "caso. Los pasos que empiezan mientras otro está en curso se cuentan como iniciados en paralelo, no "
              "como espera."},
    "el.times_elapsed": {
        "en": "Only elapsed times are available for some steps: the time since the previous completion includes "
              "waiting and work and cannot be split.",
        "es": "Para algunos pasos solo hay tiempos transcurridos: el tiempo desde la finalización anterior incluye "
              "espera y trabajo y no puede dividirse."},
    "el.warn_duplicates": {
        "en": "{n} exact duplicate events were removed (listed with the row they repeat).",
        "es": "Se han quitado {n} eventos duplicados exactos (se listan con la fila que repiten)."},
    "el.warn_ties": {
        "en": "{n} groups of steps finish at the same moment in a case: their order is not known and the graph "
              "edges between them depend on the source-row order.",
        "es": "{n} grupos de pasos terminan en el mismo momento dentro de un caso: su orden no se conoce y las "
              "aristas del grafo entre ellos dependen del orden de las filas de origen."},
    "el.warn_no_start_events": {
        "en": "{n} completions have no matching start event: their service time is unknown.",
        "es": "{n} finalizaciones no tienen evento de inicio correspondiente: su tiempo de servicio es desconocido."},
    "el.warn_no_complete_events": {
        "en": "{n} start events have no completion: those steps are in progress or the completion is missing.",
        "es": "{n} eventos de inicio no tienen finalización: esos pasos están en curso o falta la finalización."},
    "el.warn_zone_changes": {
        "en": "{n} cases change UTC offset between their events (daylight saving or another zone): instants were "
              "normalised to UTC with their own offsets.",
        "es": "{n} casos cambian de desfase UTC entre sus eventos (horario de verano u otra zona): los instantes se "
              "han normalizado a UTC con su propio desfase."},
    "el.warn_open": {
        "en": "{n} open (censored) cases are counted but left out of the time statistics and of model fitting.",
        "es": "{n} casos abiertos (censurados) se cuentan, pero quedan fuera de las estadísticas de tiempo y del "
              "ajuste del modelo."},
    "el.caveat": {
        "en": "Observed, descriptive results. A directly-follows count A → B does not prove that A causes B, and the "
              "graph is not a complete concurrency model.",
        "es": "Resultados observados y descriptivos. Un recuento de sucesión directa A → B no demuestra que A cause "
              "B, y el grafo no es un modelo completo de concurrencia."},
    "el.caveat_sim": {
        "en": "Simulated scenarios under the stated assumptions (first-order routing, independent durations, the "
              "fit period's arrivals), not safe predictions; a failed holdout check means the model should not be "
              "trusted for decisions.",
        "es": "Escenarios simulados bajo los supuestos indicados (enrutamiento de primer orden, duraciones "
              "independientes, llegadas del periodo de ajuste), no predicciones seguras; si falla el contraste con "
              "el periodo reservado, el modelo no es fiable para decidir."},
    "el.why_skipped": {
        "en": "An expected step does not appear in these closed cases.",
        "es": "Un paso esperado no aparece en estos casos cerrados."},
    "el.why_inserted": {
        "en": "A step that is not in the expected sequence appears.",
        "es": "Aparece un paso que no está en la secuencia esperada."},
    "el.why_repeated": {
        "en": "An expected step happens more than once (rework).",
        "es": "Un paso esperado ocurre más de una vez (retrabajo)."},
    "el.why_reordered": {
        "en": "Two expected steps complete in the opposite order.",
        "es": "Dos pasos esperados terminan en el orden contrario."},
    "el.why_invalid": {
        "en": "The time cannot be read (or does not exist in the declared zone): the row is left out.",
        "es": "La hora no puede leerse (o no existe en la zona declarada): la fila queda fuera."},
    "el.why_missing_ids": {
        "en": "The row has no case or no activity: it is left out.",
        "es": "La fila no tiene caso o actividad: queda fuera."},
    "el.why_lifecycle": {
        "en": "The lifecycle value is neither a start nor a completion: the row is left out.",
        "es": "El valor de ciclo de vida no es ni inicio ni finalización: la fila queda fuera."},
    "el.why_duplicate": {
        "en": "Same case, activity, time, lifecycle, resource and cost as an earlier row: removed once.",
        "es": "Mismo caso, actividad, hora, ciclo de vida, recurso y coste que una fila anterior: se quita."},
    "el.why_tie": {
        "en": "Steps that finish at the same moment: their real order is unknown.",
        "es": "Pasos que terminan en el mismo momento: su orden real se desconoce."},
    "el.why_no_start": {
        "en": "A completion without a start: its service time is unknown.",
        "es": "Una finalización sin inicio: su tiempo de servicio es desconocido."},
    "el.why_no_complete": {
        "en": "A start without a completion: the step is in progress or its end was not recorded.",
        "es": "Un inicio sin finalización: el paso está en curso o su fin no se registró."},
    "el.why_start_after_end": {
        "en": "The start is later than the end: the duration was set to zero and the row is listed.",
        "es": "El inicio es posterior al fin: la duración se ha puesto a cero y la fila se lista."},
    "el.why_zone": {
        "en": "The case's events carry different UTC offsets; they were normalised to UTC.",
        "es": "Los eventos del caso llevan desfases UTC distintos; se han normalizado a UTC."},
    "el.why_open": {
        "en": "The case has not reached an end activity: it is censored and left out of times and fitting.",
        "es": "El caso no ha llegado a una actividad final: está censurado y queda fuera de tiempos y ajuste."},
    "el.src_case_starts": {"en": "case start times", "es": "momentos de inicio de los casos"},
    "el.src_instances": {"en": "{n} instances of {act}", "es": "{n} ejecuciones de {act}"},
    "el.est_service": {
        "en": "empirical distribution of end − start per instance",
        "es": "distribución empírica de fin − inicio por ejecución"},
    "el.est_elapsed": {
        "en": "empirical distribution of the time since the previous completion (waiting plus work; start not "
              "recorded)",
        "es": "distribución empírica del tiempo desde la finalización anterior (espera más trabajo; sin inicio "
              "registrado)"},
    "el.est_routing": {
        "en": "observed share of each next step (first-order)",
        "es": "proporción observada de cada paso siguiente (primer orden)"},
    "el.bad_split": {
        "en": "split_at {value!r} is not a readable date and time",
        "es": "split_at {value!r} no es una fecha y hora legible"},
    "el.holdout_range": {
        "en": "holdout must be between 0.05 and 0.8 (share of the latest cases kept aside)",
        "es": "holdout debe estar entre 0,05 y 0,8 (proporción de los casos más recientes que se reservan)"},
    "el.few_fit": {
        "en": "only {n} closed cases before the split; at least {min} are needed to fit the model",
        "es": "solo hay {n} casos cerrados antes del corte; se necesitan al menos {min} para ajustar el modelo"},
    "el.few_holdout": {
        "en": "only {n} closed cases after the split; at least {min} are needed to check the model",
        "es": "solo hay {n} casos cerrados después del corte; se necesitan al menos {min} para contrastar el "
              "modelo"},
    "el.bad_tolerance": {
        "en": "unknown tolerance {key!r}; known: {keys}", "es": "tolerancia desconocida {key!r}; conocidas: {keys}"},
    "el.bad_tolerance_value": {
        "en": "tolerance {key!r} must be a number between 0 and 5 (relative difference)",
        "es": "la tolerancia {key!r} debe ser un número entre 0 y 5 (diferencia relativa)"},
    "el.not_evaluable": {
        "en": "not evaluable (no observed or simulated value)",
        "es": "no evaluable (sin valor observado o simulado)"},
    "el.wait_not_identifiable": {
        "en": "waiting before {act} is not identifiable without start times: not part of the verdict",
        "es": "la espera antes de {act} no es identificable sin horas de inicio: no cuenta para el veredicto"},
    "el.calib_pass": {
        "en": "The model fitted on the first part reproduces the {test} held-out cases on all {n} checked metrics "
              "within tolerance.",
        "es": "El modelo ajustado con la primera parte reproduce los {test} casos reservados en las {n} métricas "
              "contrastadas dentro de la tolerancia."},
    "el.calib_fail": {
        "en": "Calibration FAILED: the model fitted on the first part does not reproduce the {test} held-out cases "
              "on {metrics}. Do not use its scenarios for decisions until the cause is found.",
        "es": "La calibración HA FALLADO: el modelo ajustado con la primera parte no reproduce los {test} casos "
              "reservados en {metrics}. No uses sus escenarios para decidir hasta encontrar la causa."},
    "el.warn_small_holdout": {
        "en": "Only {n} held-out cases: the observed percentiles are uncertain.",
        "es": "Solo hay {n} casos reservados: los percentiles observados son inciertos."},
    "el.alt_uncalibrated": {
        "en": "The baseline failed the holdout check: this comparison is indicative only.",
        "es": "La línea base no ha superado el contraste con el periodo reservado: esta comparación es solo "
              "orientativa."},
    "el.split_rule": {
        "en": "Closed cases are ordered by start time; those that start before the split fit the model and the "
              "rest are held out.",
        "es": "Los casos cerrados se ordenan por momento de inicio; los que empiezan antes del corte ajustan el modelo "
              "y el resto se reservan."},
    "el.calib_method": {
        "en": "The model is fitted only on the cases before the split (durations, routing shares, capacity, "
              "arrivals), simulated with the scenario engine and compared with the held-out cases. Alternatives use "
              "the same seed and replications as the baseline (common random numbers) and show 95% intervals of the "
              "difference.",
        "es": "El modelo se ajusta solo con los casos anteriores al corte (duraciones, proporciones de ruta, "
              "capacidad, llegadas), se simula con el motor de escenarios y se compara con los casos reservados. Las "
              "alternativas usan la misma semilla y réplicas que la línea base (números aleatorios comunes) y "
              "muestran intervalos del 95 % de la diferencia."},
    "el.dataset_changed": {
        "en": "The data in {name!r} differ from the recipe's reference: results may not match the exported ones.",
        "es": "Los datos de {name!r} no coinciden con la referencia de la receta: los resultados pueden no coincidir "
              "con los exportados."},
    "el.recipe_note": {
        "en": "Recipe: mapping, options and settings with a reference to the data (name, version, fingerprint); "
              "no rows are copied.",
        "es": "Receta: correspondencia de columnas, opciones y ajustes con una referencia a los datos (nombre, "
              "versión, huella); no se copia ninguna fila."},
    "el.recipe_json": {"en": "recipe is not valid JSON: {exc}", "es": "la receta no es JSON válido: {exc}"},
    "el.recipe_kind": {
        "en": "this is not a process recipe exported by this app",
        "es": "esto no es una receta de proceso exportada por esta aplicación"},
    "el.need_dataset": {
        "en": "dataset is required (or a recipe that names it)",
        "es": "hace falta el conjunto de datos (o una receta que lo indique)"},
    # ---- model edits, pairing rule, localised row references
    'el.row_n': {"en": 'row {n}', "es": 'fila {n}'},
    'el.row_dup': {"en": 'row {n} = row {m}', "es": 'fila {n} = fila {m}'},
    'el.row_value': {"en": 'row {n}: {value}', "es": 'fila {n}: {value}'},
    'el.pairing_fifo': {"en": 'Start and complete rows of the same case and activity are paired first in, first out (the earliest open start takes the next completion). No resource column is mapped, so overlapping runs of the same step cannot be told apart.', "es": 'Las filas de inicio y fin de un mismo caso y actividad se emparejan por orden de llegada (el inicio abierto más antiguo toma la siguiente finalización). No hay columna de recurso asignada, así que las ejecuciones solapadas del mismo paso no se pueden distinguir.'},
    'el.pairing_resource': {"en": 'Start and complete rows of the same case and activity are paired by resource first (a completion takes the earliest open start with the same resource) and, when none matches or the resource is empty, first in, first out. The counts per activity say how many pairs each rule decided.', "es": 'Las filas de inicio y fin de un mismo caso y actividad se emparejan primero por recurso (una finalización toma el inicio abierto más antiguo con el mismo recurso) y, si ninguno coincide o el recurso está vacío, por orden de llegada. Los recuentos por actividad indican cuántas parejas decidió cada regla.'},
    'el.edit_invalid': {"en": 'Model edit rejected at {where}: {reason}', "es": 'Edición del modelo rechazada en {where}: {reason}'},
    'el.edit_shape': {"en": 'model_edits must be an object with any of: {keys}', "es": 'model_edits debe ser un objeto con alguna de estas claves: {keys}'},
    'el.edit_unknown_key': {"en": 'unknown key {key!r}; allowed: {keys}', "es": 'clave desconocida {key!r}; permitidas: {keys}'},
    'el.edit_rate_number': {"en": 'the arrival rate must be a finite number (cases per hour)', "es": 'la tasa de llegadas debe ser un número finito (casos por hora)'},
    'el.edit_rate_positive': {"en": 'the arrival rate must be greater than zero', "es": 'la tasa de llegadas debe ser mayor que cero'},
    'el.edit_stations_shape': {"en": 'stations must be an object {{activity: {{service?, servers?}}}}', "es": 'stations debe ser un objeto {{actividad: {{service?, servers?}}}}'},
    'el.edit_unknown_activity': {"en": 'unknown activity {act!r}; the model has: {acts}', "es": 'actividad desconocida {act!r}; el modelo tiene: {acts}'},
    'el.edit_station_shape': {"en": 'give at least one of: {keys}', "es": 'indica al menos uno de: {keys}'},
    'el.edit_station_key': {"en": 'unknown key {key!r}; allowed: {keys}', "es": 'clave desconocida {key!r}; permitidas: {keys}'},
    'el.edit_servers_int': {"en": 'capacity must be a whole number of parallel resources', "es": 'la capacidad debe ser un número entero de recursos en paralelo'},
    'el.edit_servers_min': {"en": 'capacity must be at least 1', "es": 'la capacidad debe ser al menos 1'},
    'el.edit_service_shape': {"en": 'give a distribution {{dist, ...}} with dist one of: {dists} (hours); for empirical give values: [..] or edit the observed sample value by value with service_values', "es": 'indica una distribución {{dist, ...}} con dist entre: {dists} (horas); para empírica indica values: [..] o edita la muestra observada valor a valor con service_values'},
    'el.edit_routing_shape': {"en": 'routing must be an object {{start?, next?}} with shares per activity', "es": 'routing debe ser un objeto {{start?, next?}} con proporciones por actividad'},
    'el.edit_shares_shape': {"en": 'shares must be a non-empty object {{activity: share}}', "es": 'las proporciones deben ser un objeto no vacío {{actividad: proporción}}'},
    'el.edit_share_unknown': {"en": 'unknown target {name!r}; allowed: {names}', "es": 'destino desconocido {name!r}; permitidos: {names}'},
    'el.edit_share_number': {"en": 'the share of {name!r} must be a finite number', "es": 'la proporción de {name!r} debe ser un número finito'},
    'el.edit_share_negative': {"en": 'the share of {name!r} cannot be negative', "es": 'la proporción de {name!r} no puede ser negativa'},
    'el.edit_share_sum': {"en": 'the shares add up to zero: at least one must be positive', "es": 'las proporciones suman cero: al menos una debe ser positiva'},
    'el.edited_estimator': {"en": "edited by the user (the fitted value and its source are kept under 'fitted')", "es": "editado por la persona usuaria (el valor ajustado y su origen se conservan en 'fitted')"},
    'el.edit_diff_note': {"en": 'Both runs use the same seed, replications and holdout cases; only the edited parameters differ.', "es": 'Ambas simulaciones usan la misma semilla, réplicas y casos reservados; solo difieren los parámetros editados.'},
    'el.edit_verdict': {"en": 'The model was edited by the user ({n} parameters): this verdict is about the edited model, not the fitted one.', "es": 'El modelo fue editado por la persona usuaria ({n} parámetros): este veredicto es del modelo editado, no del ajustado.'},
    'el.edit_flip_same': {"en": 'The verdict is the same as with the fitted model.', "es": 'El veredicto es el mismo que con el modelo ajustado.'},
    'el.edit_flip_gain': {"en": 'The edited model passes the holdout check; the fitted one did not.', "es": 'El modelo editado supera la comprobación con los casos reservados; el ajustado no.'},
    'el.edit_flip_loss': {"en": 'The edited model fails the holdout check; the fitted one passed.', "es": 'El modelo editado no supera la comprobación con los casos reservados; el ajustado sí.'},
    'el.warn_edited': {"en": '{n} parameters of the model were edited by the user and are not estimated from the data.', "es": '{n} parámetros del modelo fueron editados por la persona usuaria y no se estiman a partir de los datos.'},
})


# ======================================================================= fabrication quotes (fq.*)
register({
    "fq.caveat": {
        "en": "A quote by scenario under the stated parameters: every figure follows from the listed formulas, units and "
              "sources. Assumed values are placeholders to be replaced; nothing here is a measured cost until actuals are recorded.",
        "es": "Presupuesto por escenarios bajo los parámetros indicados: cada cifra sale de las fórmulas, unidades y "
              "fuentes enumeradas. Los valores supuestos son provisionales y deben sustituirse; nada de esto es un coste "
              "medido hasta que se registren los reales."},
    "fq.name": {
        "en": "the quote name must start with a letter or digit and use only letters, digits, spaces, _ . - (max 60)",
        "es": "el nombre del presupuesto debe empezar por letra o dígito y usar solo letras, dígitos, espacios, _ . - (máx. 60)"},
    "fq.unknown_process": {
        "en": "unknown process {process!r}; choose one of {valid}",
        "es": "proceso desconocido {process!r}; elige uno de {valid}"},
    "fq.unknown_quote": {
        "en": "unknown quote {name!r}; saved: {saved}", "es": "presupuesto desconocido {name!r}; guardados: {saved}"},
    "fq.exists": {
        "en": "a quote named {name!r} already exists (use replace to overwrite it)",
        "es": "ya existe un presupuesto llamado {name!r} (usa replace para sobrescribirlo)"},
    "fq.bad_spec": {"en": "the quote definition must be an object", "es": "la definición del presupuesto debe ser un objeto"},
    "fq.bad_currency": {"en": "unsupported currency {currency!r} (EUR or USD)", "es": "moneda no admitida {currency!r} (EUR o USD)"},
    "fq.bad_policy": {
        "en": "volume_only must be one of {valid}, not {value!r}",
        "es": "volume_only debe ser uno de {valid}, no {value!r}"},
    "fq.bad_param_name": {
        "en": "invalid name {name!r}: use a letter or _ first, then letters, digits or _",
        "es": "nombre no válido {name!r}: empieza por letra o _ y sigue con letras, dígitos o _"},
    "fq.bad_param": {"en": "parameter {name}: {why}", "es": "parámetro {name}: {why}"},
    "fq.not_a_number": {"en": "the value must be a finite number or empty", "es": "el valor debe ser un número finito o estar vacío"},
    "fq.bad_source": {
        "en": "unknown source {source!r}; use one of {valid}", "es": "fuente desconocida {source!r}; usa una de {valid}"},
    "fq.unknown_param": {
        "en": "unknown parameter {name!r}; known: {known}", "es": "parámetro desconocido {name!r}; conocidos: {known}"},
    "fq.unit_mismatch": {
        "en": "{name}: the unit {unit!r} cannot be converted to {expected!r}",
        "es": "{name}: la unidad {unit!r} no se puede convertir a {expected!r}"},
    "fq.dup_id": {"en": "the name {name!r} is used twice", "es": "el nombre {name!r} está repetido"},
    "fq.bad_category": {
        "en": "unknown category {category!r}; use one of {valid}", "es": "categoría desconocida {category!r}; usa una de {valid}"},
    "fq.no_formula": {"en": "{name} has no formula", "es": "{name} no tiene fórmula"},
    "fq.formula_syntax": {
        "en": "formula {formula!r} is not valid: {detail}", "es": "la fórmula {formula!r} no es válida: {detail}"},
    "fq.formula_forbidden": {
        "en": "formula element not allowed: {what} (only numbers, names, + - * / **, min, max, abs, ceil, floor, round, sqrt, "
              "and a comparison inside «a if b < c else d»)",
        "es": "elemento de fórmula no permitido: {what} (solo números, nombres, + - * / **, min, max, abs, ceil, floor, "
              "round, sqrt y una comparación dentro de «a if b < c else d»)"},
    "fq.unknown_name": {"en": "unknown name {name!r} in a formula", "es": "nombre desconocido {name!r} en una fórmula"},
    "fq.unknown_name_in_formula": {
        "en": "{item}: the formula uses {name!r}, which is not a parameter, derived quantity or item",
        "es": "{item}: la fórmula usa {name!r}, que no es un parámetro, una magnitud derivada ni una partida"},
    "fq.cycle": {"en": "the formulas depend on each other in a loop: {path}", "es": "las fórmulas dependen unas de otras en bucle: {path}"},
    "fq.div_zero": {"en": "division by zero in a formula", "es": "división por cero en una fórmula"},
    "fq.need_dimensionless": {
        "en": "{what} needs a dimensionless value", "es": "{what} necesita un valor sin unidades"},
    "fq.eval_error": {
        "en": "cannot evaluate {formula!r}: {detail}", "es": "no se puede evaluar {formula!r}: {detail}"},
    "fq.blocked_missing": {
        "en": "blocked: missing {missing}. Nothing is estimated for a measurement that was not given.",
        "es": "bloqueada: falta {missing}. No se estima ninguna medida que no se haya dado."},
    "fq.blocked_concrete_volume": {
        "en": "blocked: concrete print time is not derived from the solid volume. It needs the toolpath length, the print speed "
              "and the layer count or height (missing: {missing}).",
        "es": "bloqueada: el tiempo de impresión de hormigón no se deduce del volumen sólido. Necesita la longitud de "
              "trayectoria, la velocidad de impresión y el número o la altura de capa (falta: {missing})."},
    "fq.blocked_coarse_policy": {
        "en": "blocked by option: {id} could only be estimated coarsely from volume and the quote is set to block coarse estimates",
        "es": "bloqueada por opción: {id} solo se podía estimar de forma gruesa a partir del volumen y el presupuesto está "
              "configurado para bloquear estimaciones gruesas"},
    "fq.warn_coarse_time": {
        "en": "coarse estimate: the print time comes from the volume alone with an assumed throughput. Give a slicer time, or "
              "layer height, speed and part height, for a real figure.",
        "es": "estimación gruesa: el tiempo de impresión sale solo del volumen con un rendimiento supuesto. Aporta el "
              "tiempo del segmentador, o altura de capa, velocidad y altura de la pieza, para una cifra real."},
    "fq.warn_coarse_fill": {
        "en": "coarse estimate: the printed volume uses an assumed fill fraction because the surface area is missing",
        "es": "estimación gruesa: el volumen impreso usa una fracción de relleno supuesta porque falta la superficie"},
    "fq.warn_incomplete": {
        "en": "the total is partial: these items are blocked: {items}", "es": "el total es parcial: estas partidas están bloqueadas: {items}"},
    "fq.warn_assumed": {
        "en": "{n} parameters in use are assumed, not measured or quoted: {names}",
        "es": "{n} parámetros en uso son supuestos, no medidos ni de tarifa: {names}"},
    "fq.concrete_rule": {
        "en": "concrete printing needs a «print_time_h» quantity whose every formula uses toolpath_length_m and "
              "print_speed_mm_s and is not a coarse estimate: print time is never derived from the volume",
        "es": "la impresión de hormigón necesita una magnitud «print_time_h» cuyas fórmulas usen todas "
              "toolpath_length_m y print_speed_mm_s y no sean una estimación gruesa: el tiempo nunca se deduce del volumen"},
    "fq.nothing_to_set": {"en": "no parameters given", "es": "no se ha dado ningún parámetro"},
    "fq.scenario_name": {
        "en": "scenario names use letters, digits, spaces, _ . - (max 40) and cannot be «Base»",
        "es": "los nombres de escenario usan letras, dígitos, espacios, _ . - (máx. 40) y no pueden ser «Base»"},
    "fq.scenario_empty": {"en": "a scenario needs at least one parameter override", "es": "un escenario necesita al menos un parámetro modificado"},
    "fq.scenario_unknown": {
        "en": "unknown scenario {scenario!r}; saved: {known}", "es": "escenario desconocido {scenario!r}; guardados: {known}"},
    "fq.scenario_none": {"en": "no scenarios saved yet; save one first", "es": "aún no hay escenarios guardados; guarda uno primero"},
    "fq.bad_pct": {"en": "pct must be between 0 and 100 (exclusive)", "es": "pct debe estar entre 0 y 100 (sin incluirlos)"},
    "fq.sens_missing": {"en": "the parameter has no value", "es": "el parámetro no tiene valor"},
    "fq.sens_no_params": {"en": "no parameter to vary", "es": "no hay ningún parámetro que variar"},
    "fq.sens_method": {
        "en": "One at a time: each parameter set to -{pct}% and +{pct}% of its value while the rest stay as they are; only the "
              "items that depend on it are recomputed.",
        "es": "De uno en uno: cada parámetro al -{pct} % y al +{pct} % de su valor mientras el resto se mantiene; solo se "
              "recalculan las partidas que dependen de él."},
    "fq.actual_unknown_item": {
        "en": "unknown item {item!r}; known: {known}", "es": "partida desconocida {item!r}; conocidas: {known}"},
    "fq.actual_unknown": {"en": "no actual record with id {id}", "es": "no hay ningún real con id {id}"},
    "fq.actual_value": {"en": "the actual value must be a finite number", "es": "el valor real debe ser un número finito"},
    "fq.actual_unit": {
        "en": "{item}: the unit {unit!r} cannot be converted to {expected!r}",
        "es": "{item}: la unidad {unit!r} no se puede convertir a {expected!r}"},
    "fq.note_vulcan_model": {"en": "Vulcan model {mid}", "es": "Modelo {mid} de Vulcan"},
    "fq.note_vulcan_bbox": {"en": "Vulcan bbox_{axis} ({units})", "es": "Caja envolvente de Vulcan, eje {axis} ({units})"},
    "fq.note_blank_envelope": {"en": "envelope of the part (bbox_x * bbox_y): not its true area",
                               "es": "envolvente de la pieza (bbox_x × bbox_y): no es su área real"},
    "fq.actuals_subset": {
        "en": "Only items that have recorded actuals are compared, so the totals are not diluted by items still open.",
        "es": "Solo se comparan las partidas con reales registrados, para que los totales no se diluyan con partidas aún abiertas."},
    "fq.calibration_note": {
        "en": "Actuals are facts kept next to the quote for calibration. The ratio actual/quote suggests which assumption "
              "to revisit; no parameter is changed automatically and nothing is written to the books.",
        "es": "Los reales son hechos guardados junto al presupuesto para calibrar. La razón real/presupuesto indica qué "
              "supuesto revisar; ningún parámetro se cambia solo y no se escribe nada en la contabilidad."},
    "fq.export_format": {"en": "unknown export format {fmt!r}; use json, csv or markdown", "es": "formato de exportación desconocido {fmt!r}; usa json, csv o markdown"},
    "fq.md_params": {"en": "Parameters", "es": "Parámetros"},
    "fq.md_items": {"en": "Line items and derived quantities", "es": "Partidas y magnitudes derivadas"},
    "fq.md_total": {"en": "Total", "es": "Total"},
    "fq.md_partial": {"en": "partial: some items are blocked", "es": "parcial: hay partidas bloqueadas"},
    "fq.md_warnings": {"en": "Warnings", "es": "Avisos"},
    "fq.vulcan_model_id": {"en": "a Vulcan model id is required", "es": "hace falta el identificador del modelo de Vulcan"},
    "fq.vulcan_http": {"en": "Vulcan answered {code} for {url}", "es": "Vulcan respondió {code} en {url}"},
    "fq.vulcan_unreachable": {
        "en": "Vulcan is not reachable at {url} ({detail}). Give the measurements explicitly instead.",
        "es": "Vulcan no responde en {url} ({detail}). Da las medidas de forma explícita."},
    "fq.vulcan_bad_payload": {"en": "Vulcan returned something that is not a model description ({url})", "es": "Vulcan devolvió algo que no es la descripción de un modelo ({url})"},
    "fq.vulcan_field_missing": {
        "en": "Vulcan gave no {field}: it stays missing and blocks the items that need it",
        "es": "Vulcan no dio {field}: queda sin valor y bloquea las partidas que lo necesitan"},
    "fq.vulcan_units_unknown": {
        "en": "Vulcan's units guess is {value!r}: the bounding box was not used because its unit is not known",
        "es": "la unidad estimada por Vulcan es {value!r}: no se usa la caja envolvente porque se desconoce su unidad"},
    "fq.vulcan_not_watertight": {
        "en": "the mesh is not watertight: its volume may be wrong, check it before relying on material figures",
        "es": "la malla no es estanca: su volumen puede ser erróneo, compruébalo antes de fiarte de las cifras de material"},
    "fq.vulcan_no_inspection": {
        "en": "the destination report of Vulcan could not be read ({detail})",
        "es": "no se pudo leer el informe de destino de Vulcan ({detail})"},
})


# ======================================================================= arrival seasonality and empirical edits (sn.*)
register({
    "sn.method": {
        "en": "Arrivals (first event of each case) are counted per local hour of the day and per weekday in the declared zone. "
              "Each pattern is compared with a constant rate by a Pearson chi-square test whose expected counts are proportional "
              "to the hours of each slot actually observed. A pattern counts as significant when p is below alpha and every "
              "expected count is at least 5.",
        "es": "Las llegadas (primer evento de cada caso) se cuentan por hora local del día y por día de la semana en la zona "
              "declarada. Cada patrón se compara con una tasa constante mediante una prueba chi-cuadrado de Pearson cuyos "
              "recuentos esperados son proporcionales a las horas realmente observadas de cada tramo. Un patrón es "
              "significativo cuando p es menor que alfa y todos los recuentos esperados son al menos 5."},
    "sn.caveat": {
        "en": "The schedule is multiplicative in hour and weekday and does not model holidays, seasons or changes of time "
              "inside the cycle. A significant pattern says the rate is not constant, not why.",
        "es": "El horario es multiplicativo en hora y día de la semana y no modela festivos, estaciones ni cambios de hora "
              "dentro del ciclo. Un patrón significativo dice que la tasa no es constante, no por qué."},
    "sn.too_few": {"en": "only {n} arrivals: at least {min} are needed to test for a pattern",
                   "es": "solo hay {n} llegadas: hacen falta al menos {min} para contrastar un patrón"},
    "sn.too_short": {"en": "the arrivals span {hours:.1f} h: at least {min:.0f} h are needed to see a daily pattern",
                     "es": "las llegadas abarcan {hours:.1f} h: hacen falta al menos {min:.0f} h para ver un patrón diario"},
    "sn.too_long": {"en": "the arrivals span {hours:.0f} h: too long to count the hours of each slot", "es": "las llegadas abarcan {hours:.0f} h: demasiado para contar las horas de cada tramo"},
    "sn.constant": {"en": "No significant pattern against a constant rate (hour of day p = {ph}, weekday p = {pd}): arrivals are modelled without seasonality.",
                    "es": "Sin patrón significativo frente a una tasa constante (hora del día p = {ph}, día de la semana p = {pd}): las llegadas se modelan sin estacionalidad."},
    "sn.pattern": {"en": "Arrivals follow a pattern by {used} (hour of day p = {ph}, weekday p = {pd}); peak hours: {peaks}.{forced}",
                   "es": "Las llegadas siguen un patrón por {used} (hora del día p = {ph}, día de la semana p = {pd}); horas punta: {peaks}.{forced}"},
    "sn.used_hour": {"en": "hour of day", "es": "hora del día"},
    "sn.used_day": {"en": "weekday", "es": "día de la semana"},
    "sn.used_hour_day": {"en": "hour of day and weekday", "es": "hora del día y día de la semana"},
    "sn.forced": {"en": " The hourly schedule was requested although the test did not reach significance.",
                  "es": " Se pidió el horario por horas aunque la prueba no alcanzó significación."},
    "sn.origin_note": {"en": "Model time 0 is local midnight (24 h cycle) or Monday 00:00 (168 h cycle) of the declared zone.",
                       "es": "El tiempo 0 del modelo es la medianoche local (ciclo de 24 h) o el lunes a las 00:00 (ciclo de 168 h) de la zona declarada."},
    "el.est_seasonal": {"en": "rate per hour of the cycle from the arrivals of the fit cases, by {used} (chi-square against a constant rate: hour p = {ph}, weekday p = {pd})",
                        "es": "tasa por hora del ciclo a partir de las llegadas de los casos de ajuste, por {used} (chi-cuadrado frente a tasa constante: hora p = {ph}, día p = {pd})"},
    "el.bad_seasonal": {"en": "seasonal_arrivals {value!r} is not valid; use one of: {valid}", "es": "seasonal_arrivals {value!r} no es válido; usa uno de: {valid}"},
    "el.edit_values_number": {"en": "{label} must be a finite number of hours", "es": "{label} debe ser un número finito de horas"},
    "el.edit_values_negative": {"en": "{label} cannot be negative", "es": "{label} no puede ser negativo"},
    "el.edit_values_not_empirical": {"en": "this distribution is not an empirical sample: give the complete list of values instead", "es": "esta distribución no es una muestra empírica: indica la lista completa de valores"},
    "el.edit_values_shape": {"en": "give the complete list of values, or an object with any of set {{index: hours}}, remove [index], add [hours]", "es": "indica la lista completa de valores, o un objeto con alguno de set {{índice: horas}}, remove [índice], add [horas]"},
    "el.edit_values_index": {"en": "index {idx!r} is not a position of the sample (0 to {n}-1)", "es": "el índice {idx!r} no es una posición de la muestra (0 a {n}-1)"},
    "el.edit_values_overlap": {"en": "the same index cannot be both set and removed", "es": "un mismo índice no puede estar a la vez en set y en remove"},
    "el.edit_values_empty": {"en": "the sample cannot be left empty", "es": "la muestra no puede quedar vacía"},
    "el.edit_values_too_many": {"en": "an empirical sample has at most {max} values", "es": "una muestra empírica tiene como máximo {max} valores"},
    "el.edit_arrivals_both": {"en": "arrival_rate and arrival_gaps cannot be edited together", "es": "arrival_rate y arrival_gaps no se pueden editar a la vez"},
    "el.edit_arrivals_not_empirical": {"en": "the fitted arrivals are of type {kind!r}, not an empirical sample of gaps: give the complete list of gaps instead", "es": "las llegadas ajustadas son de tipo {kind!r}, no una muestra empírica de intervalos: indica la lista completa de intervalos"},
    "el.edit_arrivals_zero": {"en": "the gaps between arrivals add up to zero", "es": "los intervalos entre llegadas suman cero"},
    "el.edit_service_both": {"en": "give service or service_values, not both", "es": "indica service o service_values, no ambos"},
    # ---- fabrication quotes: skin, calibration, toolpath, Vulcan inspection
    "fq.warn_skin_not_separated": {"en": "the solid top and bottom layers are not a line item of their own: give top_bottom_area_cm2 (or a slicer G-code) to separate them; until then the infill percentage covers the whole interior",
                                   "es": "las capas sólidas superior e inferior no son una partida propia: indica top_bottom_area_cm2 (o un G-code del laminador) para separarlas; hasta entonces el porcentaje de relleno cubre todo el interior"},
    "fq.calib_no_jobs": {"en": "give a list of real jobs: each with its measurements (parameters) and what it really cost or took (actuals)", "es": "indica una lista de trabajos reales: cada uno con sus medidas (parameters) y lo que costó o tardó de verdad (actuals)"},
    "fq.calib_too_many_jobs": {"en": "at most {max} jobs per calibration", "es": "como máximo {max} trabajos por calibración"},
    "fq.calib_job_no_actuals": {"en": "job {job!r} has no actuals: give at least one line item or derived quantity with its real value", "es": "el trabajo {job!r} no tiene actuals: indica al menos una partida o magnitud derivada con su valor real"},
    "fq.calib_job_sets_fit": {"en": "job {job!r} sets {name!r}, which is one of the parameters being calibrated; remove it from the job or from fit", "es": "el trabajo {job!r} fija {name!r}, que es uno de los parámetros que se calibran; quítalo del trabajo o de fit"},
    "fq.calib_actual_value": {"en": "job {job!r}: the real value of {item!r} must be a positive finite number", "es": "trabajo {job!r}: el valor real de {item!r} debe ser un número positivo y finito"},
    "fq.calib_job_blocked": {"en": "job {job!r}: the quote cannot compute {item!r} ({why}); give that job the measurements it lacks", "es": "trabajo {job!r}: el presupuesto no puede calcular {item!r} ({why}); indica en ese trabajo las medidas que faltan"},
    "fq.calib_level": {"en": "level must be between 0.5 and 0.999", "es": "level debe estar entre 0.5 y 0.999"},
    "fq.calib_fit_names": {"en": "fit must be a list of 1 to {max} different parameter names", "es": "fit debe ser una lista de 1 a {max} nombres de parámetro distintos"},
    "fq.calib_fit_missing": {"en": "{name!r} has no value to start from: give it one before calibrating it", "es": "{name!r} no tiene valor de partida: dale uno antes de calibrarlo"},
    "fq.calib_nothing_depends": {"en": "none of the real values depends on {names}: nothing can be calibrated from these jobs", "es": "ninguno de los valores reales depende de {names}: con estos trabajos no se puede calibrar nada"},
    "fq.calib_not_identifiable": {"en": "no observation depends on {names}: they are left as they were", "es": "ninguna observación depende de {names}: se dejan como estaban"},
    "fq.calib_underdetermined": {"en": "{n} observations are not enough for {p} parameters; add jobs or real values, or calibrate fewer parameters", "es": "{n} observaciones no bastan para {p} parámetros; añade trabajos o valores reales, o calibra menos parámetros"},
    "fq.calib_no_dof": {"en": "as many observations as parameters ({n} for {p}): the fit is exact but there is no uncertainty to estimate; add jobs", "es": "tantas observaciones como parámetros ({n} para {p}): el ajuste es exacto pero no hay incertidumbre que estimar; añade trabajos"},
    "fq.calib_collinear": {"en": "the parameters move together in these jobs (collinear): the fitted values are one of many that fit, so no uncertainty is given; vary the jobs or calibrate fewer parameters", "es": "los parámetros se mueven juntos en estos trabajos (colinealidad): los valores ajustados son uno entre muchos posibles, por eso no se da incertidumbre; varía los trabajos o calibra menos parámetros"},
    "fq.calib_at_bound": {"en": "{names} ended at the lower limit (almost zero): the data do not support a positive value, treat it as not calibrated", "es": "{names} terminó en el límite inferior (casi cero): los datos no respaldan un valor positivo, trátalo como no calibrado"},
    "fq.calib_cv_method": {"en": "leave-one-job-out: each job is predicted with rates fitted without it", "es": "dejar un trabajo fuera: cada trabajo se predice con tarifas ajustadas sin él"},
    "fq.calib_cv_needs_jobs": {"en": "with fewer than 3 jobs there is no error check on jobs the fit did not see", "es": "con menos de 3 trabajos no hay comprobación del error sobre trabajos que el ajuste no ha visto"},
    "fq.calib_method": {"en": "Least squares on the relative error (model - real) / real of every observation, with the quote's own formulas as the model; standard errors from the Jacobian and Student t intervals at {level}%.", "es": "Mínimos cuadrados sobre el error relativo (modelo - real) / real de cada observación, con las fórmulas del propio presupuesto como modelo; errores típicos a partir del jacobiano e intervalos t de Student al {level}%."},
    "fq.calib_caveat": {"en": "The fit explains these jobs under this model; a rate that absorbs something the model leaves out (a missing line item, a wrong volume) will look calibrated and be wrong on other parts. Check the error on jobs left out before trusting it.", "es": "El ajuste explica estos trabajos con este modelo; una tarifa que absorbe algo que el modelo no recoge (una partida que falta, un volumen mal medido) parecerá calibrada y fallará en otras piezas. Mira el error sobre trabajos dejados fuera antes de fiarte."},
    "fq.calib_note": {"en": "calibrated from {n} real jobs: {value} {unit}", "es": "calibrado con {n} trabajos reales: {value} {unit}"},
    "fq.calib_not_converged": {"en": "the fit did not converge, so it is not applied", "es": "el ajuste no convergió, por eso no se aplica"},
    "fq.gc_empty": {"en": "the G-code is empty", "es": "el G-code está vacío"},
    "fq.gc_too_big": {"en": "the file is larger than {max} MB", "es": "el fichero pesa más de {max} MB"},
    "fq.gc_inches": {"en": "the G-code switches to inches (G20): lengths are read as millimetres and may be wrong", "es": "el G-code cambia a pulgadas (G20): las longitudes se leen como milímetros y pueden estar mal"},
    "fq.gc_no_extrusion": {"en": "no extruding moves found: this does not look like a printer G-code", "es": "no hay movimientos con extrusión: no parece un G-code de impresora"},
    "fq.gc_arcs": {"en": "{n} arc moves (G2/G3) are measured as straight chords: the path is slightly short", "es": "{n} movimientos en arco (G2/G3) se miden como cuerdas rectas: el recorrido sale algo corto"},
    "fq.gc_diameter_assumed": {"en": "the filament diameter is not in the file: 1.75 mm assumed to turn length into volume", "es": "el diámetro del filamento no está en el fichero: se supone 1,75 mm para pasar de longitud a volumen"},
    "fq.gc_layers_from_z": {"en": "the layer count is taken from the distinct heights that print", "es": "el número de capas sale de las alturas distintas en que se imprime"},
    "fq.gc_no_slicer_time": {"en": "the slicer's print time is not in the file: only a rough time from the moves is available, and it is not applied", "es": "el tiempo del laminador no está en el fichero: solo hay un tiempo aproximado de los movimientos, y no se aplica"},
    "fq.gc_volume_disagrees": {"en": "the slicer reports {slicer} cm3 of filament but the moves add up to {computed} cm3: check the filament diameter", "es": "el laminador indica {slicer} cm3 de filamento pero los movimientos suman {computed} cm3: revisa el diámetro del filamento"},
    "fq.gc_no_types": {"en": "the G-code has no ;TYPE: comments, so top/bottom layers and support cannot be told apart; the total filament is used instead", "es": "el G-code no tiene comentarios ;TYPE:, así que no se distinguen capas superior/inferior ni soportes; se usa el filamento total"},
    "fq.gc_time_note": {"en": "time_h is the slicer's own estimate; time_h_from_moves is distance over feed rate without acceleration (a lower bound)", "es": "time_h es la estimación del propio laminador; time_h_from_moves es distancia entre velocidad sin aceleración (una cota inferior)"},
    "fq.tr_empty": {"en": "the trajectory is empty", "es": "la trayectoria está vacía"},
    "fq.tr_unit": {"en": "unknown unit {unit!r}; use mm, cm, m or in", "es": "unidad desconocida {unit!r}; usa mm, cm, m o in"},
    "fq.tr_shape": {"en": "a trajectory is CSV rows x,y[,z] or JSON [[x,y,z],...] / an object with a points list", "es": "una trayectoria son filas CSV x,y[,z] o JSON [[x,y,z],...] / un objeto con una lista points"},
    "fq.tr_row": {"en": "this row is not numeric: {row}", "es": "esta fila no es numérica: {row}"},
    "fq.tr_few": {"en": "a trajectory needs at least 2 points", "es": "una trayectoria necesita al menos 2 puntos"},
    "fq.tr_many": {"en": "more than {max} points", "es": "más de {max} puntos"},
    "fq.tr_flat": {"en": "the points have no height (z is constant): layers and part height are left missing", "es": "los puntos no tienen altura (z constante): capas y altura de la pieza quedan sin dato"},
    "fq.tp_kind": {"en": "kind {kind!r} is not valid; use auto, gcode or trajectory", "es": "kind {kind!r} no es válido; usa auto, gcode o trajectory"},
    "fq.tp_note": {"en": "read from {label} (sha256 {sha})", "es": "leído de {label} (sha256 {sha})"},
    "fq.tp_without_support": {"en": "without support, skirt or purge", "es": "sin soportes, falda ni purga"},
    "fq.tp_all_filament": {"en": "all the filament of the file (support and skirt included)", "es": "todo el filamento del fichero (soportes y falda incluidos)"},
    "fq.tp_support_inside": {"en": "support is already inside the filament total", "es": "los soportes ya están dentro del total de filamento"},
    "fq.tp_nothing": {"en": "this {kind} reading has nothing to fill in a {process} quote", "es": "esta lectura ({kind}) no tiene nada que rellenar en un presupuesto de {process}"},
    "fq.insp_shape": {"en": "this is not a Vulcan inspection report (no checks list)", "es": "esto no es un informe de inspección de Vulcan (falta la lista checks)"},
    "fq.insp_unreachable": {"en": "could not reach Vulcan through the Hub: {detail}", "es": "no se pudo llegar a Vulcan por el Hub: {detail}"},
    "fq.insp_failed": {"en": "Vulcan did not return an inspection: {detail}", "es": "Vulcan no devolvió la inspección: {detail}"},
})

# ======================================================================= story (story.*)
register({
    "story.title_trend": {"en": "Trend of {y} over {x}", "es": "Tendencia de {y} a lo largo de {x}"},
    "story.title_groups": {"en": "Average {y} by {x}", "es": "Media de {y} por {x}"},
    "story.title_outliers": {"en": "Outliers in {y}", "es": "Valores atípicos en {y}"},
    "story.title_concentration": {"en": "Concentration of {x}", "es": "Concentración de {x}"},
    "story.title_change": {"en": "Change in {y} between the two halves of the period", "es": "Cambio de {y} entre las dos mitades del periodo"},
    "story.trend_up": {
        "en": "The average {y} per {grain} rises from {first} in the first period to {last} in the last ({change} % over {periods} periods; Pearson p <= {p}).",
        "es": "La media de {y} por {grain} sube de {first} en el primer periodo a {last} en el último ({change} % en {periods} periodos; p de Pearson <= {p})."},
    "story.trend_down": {
        "en": "The average {y} per {grain} falls from {first} in the first period to {last} in the last ({change} % over {periods} periods; Pearson p <= {p}).",
        "es": "La media de {y} por {grain} baja de {first} en el primer periodo a {last} en el último ({change} % en {periods} periodos; p de Pearson <= {p})."},
    "story.groups": {
        "en": "The average {y} is {highest} in {high} and {lowest} in {low}: a gap of {gap} ({relative} %; Welch p <= {p}, {groups} groups).",
        "es": "La media de {y} es {highest} en {high} y {lowest} en {low}: una diferencia de {gap} ({relative} %; p de Welch <= {p}, {groups} grupos)."},
    "story.outliers": {
        "en": "{outliers} of {rows} values of {y} ({share} %) lie outside 1.5 IQR above the third quartile ({q3}); the largest is {max}.",
        "es": "{outliers} de {rows} valores de {y} ({share} %) quedan fuera de 1,5 IQR por encima del tercer cuartil ({q3}); el mayor es {max}."},
    "story.concentration": {
        "en": "{top} holds {share} % of the rows ({top_count} of {rows}); the three largest {x} values hold {top_three} % ({groups} categories).",
        "es": "{top} concentra el {share} % de las filas ({top_count} de {rows}); los tres valores mayores de {x} suman el {top_three} % ({groups} categorías)."},
    "story.change_up": {
        "en": "The average {y} goes from {before} in the first half of the period to {after} in the second ({relative} %; Welch p <= {p}).",
        "es": "La media de {y} pasa de {before} en la primera mitad del periodo a {after} en la segunda ({relative} %; p de Welch <= {p})."},
    "story.change_down": {
        "en": "The average {y} falls from {before} in the first half of the period to {after} in the second ({relative} %; Welch p <= {p}).",
        "es": "La media de {y} baja de {before} en la primera mitad del periodo a {after} en la segunda ({relative} %; p de Welch <= {p})."},
    "story.half_before": {"en": "first half", "es": "primera mitad"},
    "story.half_after": {"en": "second half", "es": "segunda mitad"},
    "story.grain_day": {"en": "day", "es": "día"},
    "story.grain_month": {"en": "month", "es": "mes"},
    "story.grain_year": {"en": "year", "es": "año"},
    "story.no_numeric": {"en": "this dataset has no numeric column to summarise", "es": "este conjunto no tiene ninguna columna numérica que resumir"},
    "story.too_few_rows": {"en": "{rows} rows is below the {minimum} a finding needs", "es": "{rows} filas no llegan a las {minimum} que necesita un hallazgo"},
    "story.no_findings": {
        "en": "No finding clears the minimum support: no trend, group difference, outlier, concentration or change reaches the stated threshold. Nothing is written rather than something unsupported.",
        "es": "Ningún hallazgo alcanza el soporte mínimo: ni tendencia, ni diferencia entre grupos, ni valor atípico, ni concentración, ni cambio llegan al umbral indicado. Se deja sin escribir antes que escribir algo sin respaldo."},
    "story.unknown": {"en": "unknown story: {id}", "es": "relato desconocido: {id}"},
    "story.unknown_finding": {"en": "the story has no finding {id}", "es": "el relato no tiene el hallazgo {id}"},
    "story.statement_rejected": {
        "en": "the sentence was discarded because these numbers are not among the finding's figures: {numbers}. The template stays.",
        "es": "se descartó la frase porque estas cifras no están entre las del hallazgo: {numbers}. Queda la plantilla."},
    "story.statement_kept": {"en": "the sentence was checked against the finding's figures and kept", "es": "la frase se comprobó contra las cifras del hallazgo y se conserva"},
    "story.no_model": {"en": "no language model is available: the sentences stay as the templates", "es": "no hay ningún modelo de lenguaje disponible: las frases quedan como las plantillas"},
    "story.cicero_unavailable": {
        "en": "Cicero's Hoard is not available through the Hub, so the presentation was not built; the HTML export is ready.",
        "es": "Cicero's Hoard no está disponible por el Hub, así que no se construyó la presentación; la exportación HTML está lista."},
    "story.cicero_failed": {"en": "Cicero's Hoard answered with an error: {detail}", "es": "Cicero's Hoard respondió con un error: {detail}"},
    "story.cicero_deck": {"en": "presentation {deck_id} in Cicero's Hoard", "es": "presentación {deck_id} en Cicero's Hoard"},
    "story.export_empty": {"en": "the story has no findings to export", "es": "el relato no tiene hallazgos que exportar"},
    "story.export_query": {"en": "Query that produces this finding", "es": "Consulta que produce este hallazgo"},
    "story.export_rows": {"en": "rows", "es": "filas"},
    "story.export_note": {
        "en": "Every figure comes from the query shown with its finding, run over the dataset's current version. A finding that did not reach the stated minimum support was left out.",
        "es": "Cada cifra sale de la consulta que acompaña a su hallazgo, ejecutada sobre la versión actual del conjunto. Se dejó fuera todo hallazgo que no alcanzó el soporte mínimo indicado."},
    "story.cicero_brief": {
        "en": "Findings computed from the dataset {dataset} ({rows} rows) in Nightingale's Hoard, one per slide, each with the sentence that states its figure and the chart of the same numbers.",
        "es": "Hallazgos calculados sobre el conjunto {dataset} ({rows} filas) en Nightingale's Hoard, uno por diapositiva, cada uno con la frase que dice su cifra y el gráfico de esos mismos números."},
    "story.cicero_notes": {
        "en": "Figure and chart come from this query over the dataset's current version: {sql}",
        "es": "La cifra y el gráfico salen de esta consulta sobre la versión actual del conjunto: {sql}"},
    "story.rewrite_unavailable": {
        "en": "the sentences stay as the templates: {detail}",
        "es": "las frases quedan como las plantillas: {detail}"},
    "story.rewrite_prompt": {
        "en": "Rewrite each sentence in {lang} so it reads naturally, using only the figures given. Do not add, round differently or invent any number, name or date. Answer with one line per finding: the id, a tab, the sentence.",
        "es": "Reescribe cada frase en {lang} para que se lea con naturalidad, usando solo las cifras dadas. No añadas, redondees de otra forma ni inventes ninguna cifra, nombre o fecha. Responde una línea por hallazgo: el id, un tabulador y la frase."},
})

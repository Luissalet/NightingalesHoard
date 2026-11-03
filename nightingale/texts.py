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

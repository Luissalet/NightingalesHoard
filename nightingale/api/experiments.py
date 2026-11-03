"""/api/twin/*, /api/experiment/*, /api/effect/* - learned dynamic twins, experimental campaigns, effect of a change."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import effects as ef
from .. import experiments as ex
from .. import twin as tw
from .deps import api_errors, services

router = APIRouter(prefix="/api")


class TwinFit(BaseModel):
    dataset: str
    time: Optional[str] = None
    outputs: list[str]
    inputs: list[str]
    model: str = "arx"
    max_lag: Optional[int] = Field(None, ge=1, le=12)
    validation_fraction: float = 0.25
    input_lag_start: int = 1
    n_boot: int = Field(200, ge=20, le=500)
    seed: int = 0
    name: Optional[str] = None


class TwinSim(BaseModel):
    name: str
    profile: Optional[dict[str, Any]] = None
    steps: Optional[int] = Field(None, ge=1, le=5000)
    initial: Optional[dict[str, Any]] = None
    level: Optional[float] = Field(None, gt=0.5, lt=1)
    n_paths: int = Field(300, ge=20, le=2000)
    seed: int = 0
    allow_extrapolation: bool = False
    max_extrapolation: float = Field(0.25, ge=0, le=5)
    save_as: Optional[str] = None


@router.post("/twin/fit")
@api_errors
def twin_fit(request: Request, b: TwinFit):
    return tw.fit_twin(services(request), b.dataset, b.time, b.outputs, b.inputs, b.model, b.max_lag, b.validation_fraction,
                       b.input_lag_start, b.n_boot, b.seed, b.name)


@router.get("/twin/models")
@api_errors
def twin_list(request: Request):
    return tw.list_twins(services(request))


@router.get("/twin/models/{name}")
@api_errors
def twin_get(request: Request, name: str):
    return tw.get_twin(services(request), name)


@router.delete("/twin/models/{name}")
@api_errors
def twin_delete(request: Request, name: str):
    return tw.delete_twin(services(request), name)


@router.post("/twin/simulate")
@api_errors
def twin_simulate(request: Request, b: TwinSim):
    return tw.simulate_twin(services(request), b.name, b.profile, b.steps, b.initial, b.level, b.n_paths, b.seed,
                            b.allow_extrapolation, b.max_extrapolation, b.save_as)


class DesignBody(BaseModel):
    factors: list[dict[str, Any]]
    kind: str = "fractional"
    fraction: Optional[int] = None
    min_resolution: int = 4
    max_runs: Optional[int] = None
    center_points: Optional[int] = None
    variant: str = "face"
    runs: Optional[int] = None
    seed: int = 0
    randomize: bool = True
    forbidden: Optional[list[Any]] = None
    save_as: Optional[str] = None


class AnalyzeBody(BaseModel):
    dataset: str
    factors: list[Any]
    response: str
    model: str = "auto"
    level: float = Field(0.95, gt=0.5, lt=1)


class NextBody(AnalyzeBody):
    objective: str = "explore"
    n: int = Field(1, ge=1, le=20)
    forbidden: Optional[list[Any]] = None
    candidates: Optional[list[dict[str, Any]]] = None
    seed: int = 0


class ChooseBody(BaseModel):
    hypotheses: list[Any]
    tests: list[dict[str, Any]]
    threshold: float = Field(0.95, gt=0.5, le=1)


@router.post("/experiment/design")
@api_errors
def exp_design(request: Request, b: DesignBody):
    kw = b.model_dump(exclude={"factors", "kind", "save_as"})
    return ex.design(services(request), b.factors, b.kind, b.save_as, **kw)


@router.post("/experiment/analyze")
@api_errors
def exp_analyze(request: Request, b: AnalyzeBody):
    return ex.analyze(services(request), b.dataset, b.factors, b.response, b.model, b.level)


@router.post("/experiment/next")
@api_errors
def exp_next(request: Request, b: NextBody):
    return ex.next_run(services(request), b.dataset, b.factors, b.response, b.objective, b.n, b.model, b.forbidden, b.candidates, b.seed)


@router.post("/experiment/choose-test")
@api_errors
def exp_choose(request: Request, b: ChooseBody):
    return ex.choose(services(request), b.hypotheses, b.tests, b.threshold)


class EffectBody(BaseModel):
    method: str
    dataset: str
    outcome: str
    time: Optional[str] = None
    intervention: Optional[Any] = None
    group: Optional[str] = None
    treated_value: Optional[Any] = None
    unit: Optional[str] = None
    confounders: Optional[list[str]] = None
    treatment: Optional[str] = None
    placebo_time: Optional[Any] = None
    placebo_outcome: Optional[str] = None
    season_period: Optional[int] = Field(None, ge=2, le=366)
    level: float = Field(0.95, gt=0.5, lt=1)


@router.post("/effect/estimate")
@api_errors
def effect_estimate(request: Request, b: EffectBody):
    return ef.estimate(services(request), b.method, b.dataset, b.outcome, b.time, b.intervention, b.group, b.treated_value, b.unit,
                       b.confounders, b.treatment, b.placebo_time, b.placebo_outcome, b.season_period, b.level)

"""Shared helpers for the API routers."""

from __future__ import annotations

import functools
import inspect

from fastapi import HTTPException, Request

from ..services import Services


def services(request: Request) -> Services:
    return request.app.state.services


def as_http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


def api_errors(fn):
    """Turn a route's ValueError/LookupError into the right HTTP status, JSON {"error": ...}."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapped(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except (ValueError, LookupError) as exc:
                raise as_http(exc) from exc
        return awrapped

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, LookupError) as exc:
            raise as_http(exc) from exc
    return wrapped

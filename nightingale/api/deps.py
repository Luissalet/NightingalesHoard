"""Shared helpers for the API routers."""

from __future__ import annotations

import functools
import inspect

from fastapi import HTTPException, Request

from ..services import Services
from ..texts import reset_lang, set_lang


def services(request: Request) -> Services:
    return request.app.state.services


def as_http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


def _request_lang(kwargs) -> str | None:
    """Language asked for with ?lang=es on the request this route received."""
    request = kwargs.get("request")
    if request is None:
        return None
    try:
        return request.query_params.get("lang") or None
    except Exception:  # not a real request (direct call in a test)
        return None


def api_errors(fn):
    """Turn a route's ValueError/LookupError into the right HTTP status, JSON {"error": ...}.

    Also sets the message language of the request (`?lang=es`, default English) for the analysis modules.
    """
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapped(*args, **kwargs):
            token = set_lang(_request_lang(kwargs))
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except (ValueError, LookupError) as exc:
                raise as_http(exc) from exc
            finally:
                reset_lang(token)
        return awrapped

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        token = set_lang(_request_lang(kwargs))
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except (ValueError, LookupError) as exc:
            raise as_http(exc) from exc
        finally:
            reset_lang(token)
    return wrapped

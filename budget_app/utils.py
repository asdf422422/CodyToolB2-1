"""Small cross-cutting helpers used by the application."""

from functools import wraps
import logging
from time import perf_counter
from typing import Callable, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


def log_execution_time(
    function: Callable[_P, _R],
) -> Callable[_P, _R]:
    """Log elapsed time at debug level while preserving return values and errors."""

    @wraps(function)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            elapsed = perf_counter() - started
            logging.getLogger(function.__module__).debug(
                "%s completed in %.6f seconds", function.__qualname__, elapsed
            )

    return wrapper

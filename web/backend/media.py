"""Portable audio adapter; callers do not depend on executable locations."""
from .processes import worker


def request(operation, cancellation=None, **arguments):
    return worker.call("media", {"operation": operation, **arguments}, cancellation=cancellation, timeout=1800)

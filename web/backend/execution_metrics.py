"""Per-execution timing, including failed/cancelled provider calls; no request content."""
from contextlib import contextmanager
from contextvars import ContextVar

_trace = ContextVar('execution_metrics',default=None)

def current_trace():
    return _trace.get() or {'waiting_seconds':0.0,'provider_calls':[]}

def add_wait(seconds):
    trace=_trace.get()
    if trace is not None:trace['waiting_seconds']+=seconds

@contextmanager
def collect():
    token=_trace.set({'waiting_seconds':0.0,'provider_calls':[]})
    try:yield _trace.get()
    finally:_trace.reset(token)

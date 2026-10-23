"""Infrastructure available only inside an explicitly bound execution boundary.

Feature services receive collaborators directly. Context propagation is restricted
here to request/worker infrastructure (AI control, transport and request history).
"""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ExecutionResources:
    platform: object
    worker: object
    runtimes: object
    ai: object
    ocr: object
    ai_control: object
    history: object = None

    @contextmanager
    def scope(self):
        marker = _resources.set(self)
        try:
            yield self
        finally:
            _resources.reset(marker)


_resources = ContextVar('execution_resources', default=None)


def execution_resources():
    """None means a standalone tool call, with its explicit adapter defaults."""
    return _resources.get()


def start_thread(target, name):
    context = copy_context()
    thread = threading.Thread(target=context.run, args=(target,), name=name, daemon=True)
    thread.start()
    return thread

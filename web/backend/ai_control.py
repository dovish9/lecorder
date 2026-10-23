"""Application-scoped pause gate. A paused request is retried, never a cancelled job."""
import threading
import time
from web.backend.processes import CancellationToken, TranscriptionCancelled
from web.backend.execution_metrics import add_wait, current_trace


class RequestToken(CancellationToken):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    @property
    def cancelled(self):
        return super().cancelled or bool(self.parent and self.parent.cancelled)

    def check(self):
        if self.parent:
            self.parent.check()
        super().check()


class AIControl:
    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.enabled = True
        self.generation = 0
        self.active = set()
        self.closed = False
        self.listeners = []
        self.transitions = threading.RLock()

    def public(self):
        with self.condition:
            return {'enabled': self.enabled, 'generation': self.generation,
                    'state': 'enabled' if self.enabled else 'pausing' if self.active else 'paused'}

    def set_enabled(self, enabled):
        with self.transitions:
            return self._set_enabled(enabled)

    def _set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('enabled는 boolean이어야 합니다.')
        with self.condition:
            if self.closed:
                raise ValueError('앱이 종료 중입니다.')
            if enabled == self.enabled:
                return self.public()
        if enabled:
            for listener in self.listeners:
                listener(True)
        with self.condition:
            self.enabled = enabled
            self.generation += 1
            if not enabled:
                for token in self.active:
                    token.cancel()
            self.condition.notify_all()
        if not enabled:
            for listener in self.listeners:
                listener(False)
        return self.public()

    def run(self, operation, parent=None, *, kind='ai', on_wait=None):
        attempt = 0
        while True:
            waiting = time.monotonic()
            with self.condition:
                paused = not self.enabled
                try:
                    while not self.enabled and not self.closed:
                        if parent:
                            parent.check()
                        if on_wait:
                            on_wait()
                        self.condition.wait(.1)
                finally:
                    duration = time.monotonic()-waiting
                    add_wait(duration)
                    if paused:
                        current_trace().setdefault('ai_control_events', []).append({
                            'kind': kind, 'status': 'resumed' if self.enabled else 'paused_wait',
                            'at': time.time(), 'waiting_seconds': duration})
                if self.closed:
                    raise TranscriptionCancelled('앱이 종료 중입니다.')
                if parent:
                    parent.check()
                token = RequestToken(parent)
                generation = self.generation
                self.active.add(token)
            started = time.monotonic()
            wait_before = current_trace()['waiting_seconds']
            status = 'completed'
            try:
                result = operation(token)
                with self.condition:
                    token.check()
                    if generation != self.generation:
                        raise TranscriptionCancelled('AI 처리 전환')
                    return result
            except Exception:
                with self.condition:
                    interrupted = generation != self.generation
                status = 'cancelled'
                if parent:
                    parent.check()
                if not interrupted or self.closed:
                    status = 'failed'
                    raise
                status = 'paused'
                attempt += 1
            finally:
                current_trace().setdefault('ai_control_events', []).append({
                    'kind': kind, 'status': status, 'at': time.time(),
                    'seconds': max(0, time.monotonic()-started-(current_trace()['waiting_seconds']-wait_before)), 'attempt': attempt})
                with self.condition:
                    self.active.discard(token)
                    self.condition.notify_all()

    def clean_up(self, operation):
        """Keep the transition visible until owned idle processes have exited."""
        from web.backend.execution_context import start_thread
        marker = RequestToken(None)
        with self.condition:
            if self.enabled or self.closed:
                return
            self.active.add(marker)
        def finish():
            try:
                operation()
            finally:
                with self.condition:
                    self.active.discard(marker)
                    self.condition.notify_all()
        start_thread(finish, 'ai-pause-unload')

    def close(self):
        with self.condition:
            self.closed = True
            for token in self.active:
                token.cancel()
            self.condition.notify_all()


def run(operation, cancellation=None, **kwargs):
    from web.backend.execution_context import execution_resources
    services = execution_resources()
    if services:
        return services.ai_control.run(operation, cancellation, **kwargs)
    return operation(cancellation or CancellationToken())

"""Private stdin/stdout HTTP bridge; never print URLs, headers or error bodies."""
import json
import sys
from contextlib import ExitStack

import requests


def execute(spec):
    try:
        with ExitStack() as stack:
            fields = dict(spec.get("multipart") or {})
            files = None
            if "file" in fields:
                audio = stack.enter_context(open(fields.pop("file"), "rb"))
                files = {"file": ("audio.wav", audio, "audio/wav")}
            session = stack.enter_context(requests.Session())
            response = session.request(
                spec["method"], spec["url"], headers=spec.get("headers"),
                json=spec.get("payload") if not fields and not files else None,
                data=fields or None, files=files,
                timeout=(5, spec["timeout"]), allow_redirects=False,
            )
            # Do not forward an error body, which can echo credentials/source.
            if not response.ok or 300 <= response.status_code < 400:
                return {"status": response.status_code}
            try:
                return {"status": response.status_code, "body": response.json()}
            except ValueError:
                return {"error": "json"}
    except requests.Timeout:
        return {"error": "timeout"}
    except (requests.RequestException, OSError, ValueError, KeyError):
        return {"error": "connection"}


if __name__ == "__main__":
    try:
        result = execute(json.load(sys.stdin))
    except Exception:
        result = {"error": "connection"}
    json.dump(result, sys.stdout, ensure_ascii=False)

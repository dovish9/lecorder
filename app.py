#!/usr/bin/env python3

import os
import signal

os.environ.setdefault("FLASK_SKIP_DOTENV", "1")

from web.backend.config import APP_PORT
from web.backend.routes import create_app
from web.backend.ai_runtime import close_all


app = create_app()


if __name__ == "__main__":
    def stop(signum, frame):
        app.extensions["lecorder_services"].close()
        close_all()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"🎛️ Lecorder: http://127.0.0.1:{APP_PORT}")
    app.extensions["lecorder_services"].start()
    try:
        app.run(host="127.0.0.1", port=APP_PORT, threaded=True, use_reloader=False)
    finally:
        app.extensions["lecorder_services"].close()
        close_all()

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from flask import jsonify, request, send_file

from web.backend.config import text
from web.backend.files import safe_name
from web.backend.output_files import prepare_webm_playback, rename_output_files
from web.backend.library.storage import storage_catalog
from web.backend.speech.transcription import SUPPORTED_MEDIA


def register(app, *, paths, services, storage_file):
    @app.get("/api/storage")
    def storage():
        return jsonify(storage_catalog(Path(paths.get().output_dir)))


    @app.get("/api/storage/file")
    def serve_storage_file():
        try:
            return send_file(storage_file(str(request.args.get("name") or "")), conditional=True)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400


    @app.post("/api/storage/audio/prepare")
    def prepare_storage_audio():
        payload = request.get_json(silent=True) or {}
        try:
            target = storage_file(str(payload.get("name") or ""))
            if target.suffix.lower() not in SUPPORTED_MEDIA:
                raise ValueError("오디오 파일을 선택해 주세요.")
            prepared, duration = prepare_webm_playback(target)
            response: dict[str, Any] = {"ok": True, "prepared": prepared}
            if duration is not None:
                response["duration"] = duration
            return jsonify(response)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except (OSError, subprocess.SubprocessError) as error:
            return jsonify(error=f"오디오 재생 정보를 준비하지 못했습니다: {error}"), 500


    @app.delete("/api/storage/file")
    def delete_storage_file():
        try:
            target = storage_file(str(request.args.get("name") or ""))
            name = target.name
            target.unlink()
            return jsonify(ok=True, name=name)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일을 삭제하지 못했습니다: {error}"), 500


    @app.delete("/api/storage/files")
    def delete_storage_files():
        payload = request.get_json(silent=True) or {}
        names = payload.get("names")
        if (
            not isinstance(names, list)
            or not names
            or len(names) > 10
            or not all(isinstance(name, str) for name in names)
        ):
            return jsonify(error="삭제할 저장 파일을 선택해 주세요."), 400
        try:
            targets = [storage_file(str(name)) for name in dict.fromkeys(names)]
            for target in targets:
                target.unlink()
            return jsonify(ok=True, deleted_files=[target.name for target in targets])
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일을 삭제하지 못했습니다: {error}"), 500


    @app.patch("/api/storage/files")
    def rename_storage_files():
        payload = request.get_json(silent=True) or {}
        names = payload.get("names")
        title = safe_name(text(payload.get("title"), 160))
        if (
            not isinstance(names, list)
            or not names
            or len(names) > 10
            or not all(isinstance(name, str) for name in names)
        ):
            return jsonify(error="수정할 저장 파일을 선택해 주세요."), 400
        if not title:
            return jsonify(error="파일 제목을 입력해 주세요."), 400
        try:
            targets = [storage_file(str(name)) for name in dict.fromkeys(names)]
            if len({target.stem for target in targets}) != 1:
                raise ValueError("같은 녹음에 속한 파일만 함께 수정할 수 있습니다.")
            destinations, _ = rename_output_files(targets, title)
            return jsonify(
                ok=True,
                name=title,
                files=[destination.name for destination in destinations],
            )
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except (UnicodeError, ValueError) as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일 이름을 수정하지 못했습니다: {error}"), 500


    @app.post("/api/storage/reveal")
    def reveal_storage_file():
        payload = request.get_json(silent=True) or {}
        try:
            target = storage_file(str(payload.get("name") or ""))
            services.platform.reveal(target)
            return jsonify(ok=True)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except RuntimeError as error:
            return jsonify(error=str(error)), 501
        except OSError as error:
            return jsonify(error=f"파일 위치를 열지 못했습니다: {error}"), 500

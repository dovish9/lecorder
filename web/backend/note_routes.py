"""Small HTTP surface for note lifecycle and immutable revision previews."""

from pathlib import Path
from hashlib import sha256
from flask import Blueprint, jsonify, request, send_file


def note_routes(library, reviews):
    routes = Blueprint("notes", __name__)

    @routes.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @routes.errorhandler(KeyError)
    def missing(error):
        return jsonify(error=str(error.args[0])), 404

    @routes.get("/api/courses/<int:course_id>/notes")
    def listing(course_id):
        return jsonify(notes=library.list(course_id))

    @routes.post("/api/courses/<int:course_id>/notes")
    def upload(course_id):
        files = request.files.getlist("files")
        return (
            jsonify(
                note=library.register(
                    course_id,
                    [(f.filename or "", f.stream) for f in files],
                    request.form.get("title", ""),
                )
            ),
            202,
        )

    @routes.get("/api/notes/<note_id>")
    def detail(note_id):
        note = library.get(note_id)
        response = jsonify(note=note, pages=library.pages(note["revision_id"]))
        response.set_etag(sha256(response.get_data()).hexdigest())
        return response.make_conditional(request)

    @routes.get("/api/notes/<note_id>/pages/<int:number>/image")
    def page_image(note_id, number):
        note = library.get(note_id)
        revision = request.args.get("revision") or note["revision_id"]
        if library._revision(revision)["note_id"] != note_id:
            raise ValueError("잘못된 노트 버전입니다.")
        page = next((p for p in library.pages(revision) if p["number"] == number), None)
        if not page or not page.get("image"):
            raise KeyError("페이지 이미지가 없습니다.")
        folder = (library.folder / note_id).resolve()
        path = (folder / page["image"]).resolve()
        if not path.is_relative_to(folder):
            raise ValueError("잘못된 이미지 경로입니다.")
        return send_file(path)

    @routes.patch("/api/notes/<note_id>/keywords")
    def keywords(note_id):
        data = request.get_json() or {}
        return jsonify(note=library.select_keywords(note_id, data.get("excluded", [])))

    @routes.post("/api/notes/<note_id>/reanalyze")
    def reanalyze(note_id):
        return jsonify(note=library.reanalyze(note_id)), 202

    @routes.post("/api/notes/<note_id>/cancel")
    def cancel(note_id):
        return jsonify(note=library.cancel(note_id))

    @routes.delete("/api/notes/<note_id>")
    def hide(note_id):
        library.hide(note_id)
        return jsonify(ok=True)

    @routes.post("/api/notes/<note_id>/pages/<int:number>/explain")
    def explain(note_id, number):
        library.request_detail(note_id, number)
        return jsonify(ok=True), 202

    @routes.get("/api/recordings/<recording_id>/runs")
    def runs(recording_id):
        library.store.get_recording(recording_id)
        with library.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM transcription_runs WHERE recording_id=? ORDER BY started_at DESC",
                (recording_id,),
            ).fetchall()
            reviews = db.execute(
                "SELECT * FROM review_runs WHERE recording_id=? ORDER BY started_at DESC",
                (recording_id,),
            ).fetchall()
        return jsonify(
            runs=[dict(row) for row in rows], review_runs=[dict(row) for row in reviews]
        )

    @routes.post("/api/recordings/<recording_id>/review")
    def review(recording_id):
        reviews.retry(recording_id)
        return jsonify(ok=True), 202

    return routes

from __future__ import annotations


from flask import jsonify, request


def register(app, *, lectures, paths, schedule_managed_restart):
    @app.post("/api/courses")
    def create_course():
        payload = request.get_json(silent=True) or {}
        try:
            course = lectures.courses.create_course(payload.get("name", ""))
            return jsonify(ok=True, course=course.public()), 201
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.patch("/api/courses/<int:course_id>")
    def update_course(course_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            course = lectures.courses.update_course(course_id, **payload)
            return jsonify(ok=True, course=course.public())
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400


    @app.delete("/api/courses/<int:course_id>")
    def delete_course(course_id: int):
        try:
            active = lectures.courses.delete_course(course_id)
            return jsonify(ok=True, active_course_id=active)
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400


    @app.post("/api/courses/<int:course_id>/select")
    def select_course(course_id: int):
        try:
            course = lectures.courses.select_course(course_id)
            return jsonify(ok=True, course=course.public())
        except KeyError as error:
            return jsonify(error=error.args[0]), 404


    @app.put("/api/environment")
    def update_environment():
        payload = request.get_json(silent=True) or {}
        try:
            current = paths.update(**payload)
            return jsonify(
                ok=True,
                environment=current.public(),
                restart_required=True,
                restart_scheduled=False,
            )
        except ValueError as error:
            return jsonify(error=str(error)), 400

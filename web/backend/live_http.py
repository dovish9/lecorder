"""Recording-scoped live draft transport; all PCM is bounded before reading."""
from flask import jsonify, request


def register(app, service):
    @app.route('/api/recordings/<recording_id>/live', methods=['GET', 'POST'])
    def live(recording_id):
        try:
            value = service.begin(recording_id) if request.method == 'POST' else service.draft(recording_id)
            return jsonify(live=value)
        except KeyError:
            return jsonify(error='녹음을 찾을 수 없습니다.'), 404
        except (ValueError, RuntimeError) as error:
            return jsonify(error=str(error)), 409

    @app.put('/api/recordings/<recording_id>/live/<session_id>/pcm')
    def pcm(recording_id, session_id):
        if request.content_length is None or not 0 < request.content_length <= 64000:
            return jsonify(error='실시간 음성 크기가 잘못되었습니다.'), 413
        try:
            session = service.get(recording_id, session_id)
            sequence = int(request.args.get('sequence', '-1'))
            start = int(request.args.get('start', '-1'))
            generation = request.args.get('generation')
            session.push(sequence, start, request.get_data(), int(generation) if generation is not None else None)
            return jsonify(ok=True)
        except ValueError as error:
            return jsonify(error=str(error)), 409

    @app.post('/api/recordings/<recording_id>/live/<session_id>/control')
    def control(recording_id, session_id):
        try:
            value = request.get_json(silent=True) or {}
            if not isinstance(value, dict):
                raise ValueError('실시간 제어 요청은 JSON 객체여야 합니다.')
            session = service.get(recording_id, session_id)
            if value.get('stop'):
                service.end(recording_id, session_id)
            else:
                session.control(value.get('paused', session.paused) is True)
                version = value.get('displayed_version')
                if type(version) is int:
                    session.displayed(version)
            return jsonify(live=session.public())
        except (ValueError, RuntimeError) as error:
            return jsonify(error=str(error)), 409

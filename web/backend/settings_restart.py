"""Apply explicitly requested settings to unfinished work after workers stop.

Published results, completed requests and historical execution rows are untouched.
Requests whose model/input changed naturally miss their versioned checkpoint.
"""
from web.backend.result_codec import dumps as result_dumps
import json
from web.backend.ai_settings import defaults


def rebind_pending(store, settings, areas=('speech', 'llm')):
    if not areas:
        return
    with store.transaction() as db:
        for row in db.execute("SELECT id,ai_settings_json,status FROM recordings WHERE status IN ('queued','processing') OR review_status IN ('queued','processing')").fetchall():
            previous = {**defaults(), **json.loads(row['ai_settings_json'] or '{}')}
            if 'llm' in areas:
                previous['llm'] = settings['llm']
            if 'speech' in areas and row['status'] in ('queued', 'processing'):
                previous['speech'] = settings['speech']
            db.execute('UPDATE recordings SET ai_settings_json=?,llm_model=? WHERE id=?',
                       (json.dumps(previous), previous.get('llm', settings['llm'])['model'], row['id']))
        if 'llm' not in areas:
            return
        for row in db.execute("SELECT id,ai_settings FROM note_revisions WHERE status IN ('queued','extracting') OR study_status IN ('pending','analyzing')").fetchall():
            previous = {**defaults(), **json.loads(row['ai_settings'] or '{}')}
            previous['llm'] = settings['llm']
            db.execute('UPDATE note_revisions SET ai_settings=? WHERE id=?', (json.dumps(previous), row['id']))
        for row in db.execute("SELECT revision_id,number,payload FROM note_details WHERE json_extract(payload, '$.data.detail_status') IN ('pending','analyzing')").fetchall():
            payload = json.loads(row['payload'])
            payload['ai_settings'] = {**defaults(), **payload.get('ai_settings', {}), 'llm': settings['llm']}
            db.execute('UPDATE note_details SET payload=? WHERE revision_id=? AND number=?', (result_dumps(payload,"note-detail","page"), row['revision_id'], row['number']))

"""Local search projection and content-free AI usage summaries."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import threading
import unicodedata


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).casefold().split())


def strings(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ' '.join(strings(v) for v in value)
    if isinstance(value, dict):
        return ' '.join(strings(v) for k, v in value.items()
                        if k in {'text', 'markdown', 'summary', 'term', 'title', 'content'})
    return ''


class Insights:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self.error = ''
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS search_revision (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, indexed INTEGER NOT NULL);
                INSERT OR IGNORE INTO search_revision VALUES (1,0,-1);
                CREATE TABLE IF NOT EXISTS search_documents (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                    course_id INTEGER, title TEXT NOT NULL, course TEXT NOT NULL,
                    tab TEXT NOT NULL, page INTEGER, seconds REAL,
                    body TEXT NOT NULL, normalized TEXT NOT NULL, title_normalized TEXT NOT NULL);
            ''')
            for table in ('recordings', 'courses', 'lecture_notes', 'note_revisions', 'note_pages', 'note_details'):
                for event in ('INSERT', 'UPDATE', 'DELETE'):
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS search_{table}_{event} AFTER {event} ON {table}
                                   BEGIN UPDATE search_revision SET version=version+1 WHERE id=1; END''')

    def start(self):
        from .services import start_thread
        if self.thread:
            return
        def work():
            while not self.stopping.is_set():
                try:
                    self.refresh()
                    self.error = ''
                except Exception:
                    self.error = '검색 색인을 갱신하지 못했습니다.'
                self.stopping.wait(2)
        self.thread = start_thread(work, 'search-index')

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(5)

    def refresh(self):
        with self.lock, self.store.transaction() as db:
            state = db.execute('SELECT version,indexed FROM search_revision WHERE id=1').fetchone()
            if state['version'] == state['indexed']:
                return
            docs = []
            def add(kind, target, course_id, title, course, tab, page, seconds, body):
                body = str(body or '').strip()
                docs.append((kind, target, course_id, title, course or '선택 안 함', tab, page, seconds,
                             body, normalize(body), normalize(title)))
            for row in db.execute('SELECT * FROM recordings'):
                args = ('recording', row['id'], row['course_id'], row['title'], row['course_name'])
                add(*args, 'transcript', None, None, '')
                if row['transcript_text']:
                    segments = json.loads(row['segments_json'])
                    if segments:
                        for segment in segments:
                            add(*args, 'transcript', None, segment.get('start', 0), segment.get('text', ''))
                    else:
                        add(*args, 'transcript', None, None, row['transcript_text'])
            for row in db.execute('''SELECT n.*,c.name AS course,r.overview,r.keywords FROM lecture_notes n
                                    LEFT JOIN courses c ON c.id=n.course_id
                                    LEFT JOIN note_revisions r ON r.id=n.revision_id WHERE n.deleted=0'''):
                args = ('note', row['id'], row['course_id'], row['title'], row['course'])
                add(*args, 'overview', None, None, strings(json.loads(row['overview'] or '[]')))
                for term in json.loads(row['keywords'] or '[]'):
                    add(*args, 'keywords', (term.get('pages') or [None])[0], None, ' '.join([term.get('term',''),term.get('meaning') or '',*term.get('spoken_forms',[])]))
                for page in db.execute('SELECT number,payload FROM note_pages WHERE revision_id=?', (row['revision_id'],)):
                    value = json.loads(page['payload'])
                    add(*args, 'original', page['number'], None, value.get('text', ''))
                    add(*args, 'pages', page['number'], None, strings(value.get('analysis')))
                for page in db.execute('SELECT number,payload FROM note_details WHERE revision_id=?', (row['revision_id'],)):
                    add(*args, 'pages', page['number'], None, strings(json.loads(page['payload']).get('detail')))
            db.execute('DELETE FROM search_documents')
            db.executemany('INSERT INTO search_documents(kind,target,course_id,title,course,tab,page,seconds,body,normalized,title_normalized) VALUES (?,?,?,?,?,?,?,?,?,?,?)', docs)
            db.execute('UPDATE search_revision SET indexed=? WHERE id=1', (state['version'],))

    def search(self, query, course=None, kind=None, offset=0, limit=30):
        query = normalize(query)
        if not query or len(query) > 200:
            return {'results': [], 'total': 0, 'indexing': False}
        if not self.thread:
            self.refresh()
        filters = ['(instr(normalized,?)>0 OR (instr(title_normalized,?)>0 AND id=(SELECT MIN(d.id) FROM search_documents d WHERE d.kind=search_documents.kind AND d.target=search_documents.target)))']
        params = [query, query]
        if course is not None:
            filters.append('COALESCE(course_id,0)=?'); params.append(course)
        if kind:
            filters.append('kind=?'); params.append(kind)
        where = ' AND '.join(filters)
        with self.store.transaction() as db:
            count = db.execute('SELECT COUNT(*) FROM search_documents WHERE '+where, params).fetchone()[0]
            rows = db.execute('SELECT * FROM search_documents WHERE '+where+
                ' ORDER BY CASE WHEN title_normalized=? THEN 0 WHEN instr(title_normalized,?)>0 THEN 1 ELSE 2 END,title,target,page,seconds,id LIMIT ? OFFSET ?',
                [*params, query, query, limit, offset]).fetchall()
            state = db.execute('SELECT version,indexed FROM search_revision WHERE id=1').fetchone()
        results = []
        for row in rows:
            result = dict(row)
            normalized = result.pop('normalized'); result.pop('title_normalized')
            body = result.pop('body')
            # Map the normalized hit back to source characters (case folding and
            # compatibility normalization can change the character count).
            hit = normalized.find(query)
            position = 0
            if hit>45:
                for index in range(len(body)):
                    if len(normalize(body[:index]))>=hit-45:
                        position=index;break
            result['snippet'] = ('…' if position else '') + body[position:position+160] + ('…' if len(body)>position+160 else '')
            results.append(result)
        return {'results': results, 'total': count, 'indexing': state['version'] != state['indexed'], 'error': self.error}

    def usage(self, days=1, offset_minutes=0, zone_name=''):
        try:
            zone = ZoneInfo(zone_name) if zone_name else timezone(timedelta(minutes=offset_minutes))
        except (ZoneInfoNotFoundError,ValueError):
            zone = timezone(timedelta(minutes=offset_minutes))
        now = datetime.now(zone)
        since = (now.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days-1)).astimezone(timezone.utc).isoformat() if days else ''
        with self.store.transaction() as db:
            rows = db.execute('''SELECT location,provider,model,kind,COUNT(*) AS requests,
                SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN status IN ('failed','interrupted') THEN 1 ELSE 0 END) AS failed,
                SUM(seconds) AS seconds,SUM(audio_seconds) AS audio_seconds,
                SUM(CASE WHEN seconds IS NULL THEN 1 ELSE 0 END) AS unknown_time,
                SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,
                SUM(CASE WHEN kind='llm' AND (input_tokens IS NULL OR output_tokens IS NULL) THEN 1 ELSE 0 END) AS unknown_usage
                FROM ai_requests WHERE started_at>=? AND id IN
                  (SELECT MIN(id) FROM ai_requests GROUP BY CASE WHEN source_id='' THEN id ELSE source_id END)
                GROUP BY location,provider,model,kind
                ORDER BY location,provider,model,kind''', (since,)).fetchall()
            began = db.execute("SELECT MIN(started_at) FROM ai_requests WHERE source_id NOT LIKE 'legacy-%'").fetchone()[0]
            partial = bool(db.execute("SELECT 1 FROM ai_requests WHERE source_id LIKE 'legacy-%' LIMIT 1").fetchone())
        return {'groups': [dict(r) for r in rows], 'recording_since': began, 'days': days, 'partial_history':partial,
                'note': '요청 기준 집계입니다. 재시도 음성과 취소 전 처리 시간이 포함됩니다.'}

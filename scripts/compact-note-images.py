#!/usr/bin/env python3
"""Share byte-identical note renders without removing any analysis revisions."""
from contextlib import closing
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(database, folder, backup):
    before = sum(p.stat().st_size for p in folder.rglob("*.png"))
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT 1 FROM note_revisions WHERE status IN ('queued','extracting') OR study_status IN ('pending','analyzing') LIMIT 1").fetchone()
        if active:
            raise RuntimeError('분석 대기/진행 작업을 마친 후 정리하세요.')
        # A separate connection backs up the committed state, before path changes.
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise ValueError('백업 파일이 이미 존재합니다.')
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        replacements = {}
        shared = {}
        rows = db.execute('''SELECT p.revision_id,p.number,p.payload,r.note_id
            FROM note_pages p JOIN note_revisions r ON r.id=p.revision_id
            JOIN lecture_notes n ON n.id=r.note_id
            ORDER BY (r.id=n.revision_id) DESC,p.number''').fetchall()
        for row in rows:
            page = json.loads(row['payload'])
            if not page.get('image'):
                continue
            home = (folder / row['note_id']).resolve()
            old = (home / page['image']).resolve()
            if not old.is_relative_to(home) or not old.is_file():
                raise ValueError(f"페이지 이미지 경로 확인 필요: {old}")
            checksum = digest(old)
            key = (str(home), checksum)
            target = shared.get(key)
            if target is None:
                target = home / 'pages-v1' / f"page-{row['number']}.png"
                if target.exists() and digest(target) != checksum:
                    target = home / 'pages-archive' / f'{checksum}.png'
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    temporary = target.with_suffix('.tmp')
                    shutil.copyfile(old, temporary)
                    if digest(temporary) != checksum:
                        raise OSError('이미지 복사 검증 실패')
                    temporary.replace(target)
                shared[key] = target
            if old != target:
                replacements[old] = target
            page['image'] = str(target.relative_to(home))
            db.execute('UPDATE note_pages SET payload=? WHERE revision_id=? AND number=?',
                       (json.dumps(page, ensure_ascii=False), row['revision_id'], row['number']))
        backup.with_suffix(".images.json").write_text(json.dumps({str(old): str(new) for old, new in replacements.items()}, ensure_ascii=False, indent=2))
        db.commit()
        removed = saved = 0
        for old, target in replacements.items():
            if old.exists() and digest(old) == digest(target):
                saved += old.stat().st_size
                old.unlink()
                removed += 1
                if not any(old.parent.iterdir()):
                    old.parent.rmdir()
        return {'removed_files': removed, 'reclaimed_bytes': before - sum(p.stat().st_size for p in folder.rglob('*.png')), 'shared_images': len(shared)}
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compact(args.database, args.folder, args.backup), ensure_ascii=False))

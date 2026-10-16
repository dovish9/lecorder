"""Recovery journal for the gap between filesystem publication and SQLite commit."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import uuid
from .atomic import write_text


class Publication:
    def __init__(self, store, recording, paths):
        self.folder = store.path.parent / 'publications' / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        files = []
        for index, path in enumerate(dict.fromkeys(str(p) for p in paths if p)):
            target = Path(path)
            backup = str(index) if target.is_file() else None
            if backup:
                shutil.copy2(target, self.folder / backup)
            files.append({'path': path, 'backup': backup})
        write_text(self.folder/'journal.json', json.dumps({'recording': asdict(recording), 'files': files}, ensure_ascii=False))

    def complete(self):
        shutil.rmtree(self.folder)

    def rollback(self):
        journal=json.loads((self.folder/'journal.json').read_text())
        for entry in journal['files']:
            target=Path(entry['path'])
            if entry['backup']:
                shutil.copy2(self.folder/entry['backup'],target)
            else:
                target.unlink(missing_ok=True)
        self.complete()


def recover(store):
    root = store.path.parent/'publications'
    for path in root.glob('*/journal.json'):
        journal = json.loads(path.read_text())
        expected = journal['recording']
        try:
            actual = asdict(store.get_recording(expected['id']))
        except KeyError:
            actual = None
        if actual != expected:
            for entry in journal['files']:
                target = Path(entry['path'])
                if entry['backup']:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path.parent/entry['backup'], target)
                else:
                    target.unlink(missing_ok=True)
        shutil.rmtree(path.parent)

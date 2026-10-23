"""Release identity and independent persistence/processing contracts."""
APP_VERSION = '0.3.0'
SCHEMA_VERSION = 2
BACKUP_VERSION = 2
FORMAT_VERSIONS = {name: 1 for name in (
    'segments', 'breaks', 'quality', 'note-snapshot', 'keywords', 'overview',
    'glossary', 'note-page', 'note-detail', 'approval', 'checkpoint', 'live-draft', 'metrics',
)}
POLICIES = {
    'speech': 'speech-1', 'review': 'review-1', 'page': 'notes-v5-glossary',
    'overview': 'overview-1', 'glossary': 'glossary-v2', 'live': 'live-1',
}

def public_versions():
    return {'app': APP_VERSION, 'schema': SCHEMA_VERSION,
            'formats': dict(FORMAT_VERSIONS), 'policies': dict(POLICIES), 'backup': BACKUP_VERSION}

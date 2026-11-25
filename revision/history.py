import sqlite3
from uuid import uuid4

from capture.frames import validate_label
from capture.records import recording_exists
from revision.lineage import EditionLink
from revision.targets import Amendment


def initialize_editions(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS edition_links (
            edition_id TEXT PRIMARY KEY NOT NULL,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            parent_id TEXT NOT NULL,
            beat INTEGER NOT NULL,
            value INTEGER NOT NULL CHECK (value BETWEEN 0 AND 255),
            FOREIGN KEY (recording_id, beat) REFERENCES recording_frames(recording_id, beat)
        )
    ''')


def original_edition_id(recording_id: str) -> str:
    validate_label(recording_id, 'recording ID')
    return f'original:{recording_id}'


def load_edition_link(connection: sqlite3.Connection, edition_id: str) -> EditionLink | None:
    validate_label(edition_id, 'edition ID')
    row = connection.execute('''
        SELECT recording_id, parent_id, beat, value FROM edition_links WHERE edition_id = ?
    ''', (edition_id,)).fetchone()
    if row is None:
        return None
    recording_id, parent_id, beat, value = row
    return EditionLink(edition_id, recording_id, parent_id, Amendment(beat, value))


def append_edition(
    connection: sqlite3.Connection, recording_id: str, parent_id: str, amendment: Amendment
) -> str:
    """Append a validated single-beat amendment to its selected parent."""
    validate_label(parent_id, 'parent edition ID')
    if connection.in_transaction:
        raise RuntimeError('edition registration requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        if not recording_exists(connection, recording_id):
            raise ValueError(f'unknown recording: {recording_id}')
        if parent_id != original_edition_id(recording_id):
            parent = load_edition_link(connection, parent_id)
            if parent is None or parent.recording_id != recording_id:
                raise ValueError('parent edition does not belong to the recording')
        edition_id = f'edition:{uuid4().hex}'
        connection.execute('''
            INSERT INTO edition_links (edition_id, recording_id, parent_id, beat, value)
            VALUES (?, ?, ?, ?, ?)
        ''', (edition_id, recording_id, parent_id, amendment.beat, amendment.value))
    return edition_id

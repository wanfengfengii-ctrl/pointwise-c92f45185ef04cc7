import sqlite3
from dataclasses import dataclass

from capture.decoder import OriginalReading
from capture.frames import Frame, validate_label
from capture.ledger import OriginalLedger


@dataclass(frozen=True, slots=True)
class Recording:
    recording_id: str
    frames: tuple[Frame, ...]
    ledger: OriginalLedger


def initialize_recordings(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError('recording initialization requires an idle connection')
    connection.execute('PRAGMA foreign_keys = ON')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS recordings (
                recording_id TEXT PRIMARY KEY NOT NULL,
                original_total INTEGER NOT NULL CHECK (original_total >= 0)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS recording_frames (
                recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
                position INTEGER NOT NULL CHECK (position >= 0),
                beat INTEGER NOT NULL CHECK (beat > 0),
                kind TEXT NOT NULL CHECK (kind IN ('absolute', 'delta')),
                raw BLOB NOT NULL CHECK (typeof(raw) = 'blob' AND length(raw) = 1),
                origin TEXT NOT NULL,
                original_value INTEGER NOT NULL CHECK (original_value BETWEEN 0 AND 255),
                PRIMARY KEY (recording_id, position),
                UNIQUE (recording_id, beat)
            )
        ''')


def recording_exists(connection: sqlite3.Connection, recording_id: str) -> bool:
    validate_label(recording_id, 'recording ID')
    return connection.execute(
        'SELECT 1 FROM recordings WHERE recording_id = ?', (recording_id,)
    ).fetchone() is not None


def save_recording(connection: sqlite3.Connection, recording: Recording) -> None:
    """Persist a validated recording and its original ledger together."""
    if connection.in_transaction:
        raise RuntimeError('recording intake requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        if recording_exists(connection, recording.recording_id):
            raise ValueError(f'recording already exists: {recording.recording_id}')
        connection.execute(
            'INSERT INTO recordings (recording_id, original_total) VALUES (?, ?)',
            (recording.recording_id, recording.ledger.total),
        )
        connection.executemany('''
            INSERT INTO recording_frames
                (recording_id, position, beat, kind, raw, origin, original_value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            (recording.recording_id, position, frame.beat, frame.kind, frame.raw,
             frame.origin, reading.value)
            for position, (frame, reading) in enumerate(
                zip(recording.frames, recording.ledger.readings, strict=True)
            )
        ))


def load_recording(connection: sqlite3.Connection, recording_id: str) -> Recording | None:
    validate_label(recording_id, 'recording ID')
    header = connection.execute(
        'SELECT original_total FROM recordings WHERE recording_id = ?', (recording_id,)
    ).fetchone()
    if header is None:
        return None
    rows = connection.execute('''
        SELECT beat, kind, raw, origin, original_value FROM recording_frames
        WHERE recording_id = ? ORDER BY position
    ''', (recording_id,)).fetchall()
    frames = tuple(Frame(beat, kind, raw, origin) for beat, kind, raw, origin, _ in rows)
    readings = tuple(OriginalReading(beat, value, origin) for beat, _, _, origin, value in rows)
    saturated = tuple(reading for reading in readings if reading.value == 255)
    return Recording(recording_id, frames, OriginalLedger(readings, header[0], saturated))

import sqlite3
from typing import Iterable

from capture.decoder import decode_recording
from capture.frames import Frame, validate_label
from capture.ledger import build_original_ledger
from capture.records import Recording, save_recording


def ingest(
    connection: sqlite3.Connection, recording_id: str, frames: Iterable[Frame]
) -> Recording:
    validate_label(recording_id, 'recording ID')
    try:
        frames = tuple(frames)
    except TypeError as exc:
        raise ValueError('frames must be supplied in device order') from exc
    ledger = build_original_ledger(decode_recording(frames))
    recording = Recording(recording_id, frames, ledger)
    save_recording(connection, recording)
    return recording

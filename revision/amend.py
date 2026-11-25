import sqlite3

from capture.records import load_recording
from revision.history import append_edition
from revision.targets import validate_target


def amend(
    connection: sqlite3.Connection, recording_id: str, parent_id: str, beat: int, value: int
) -> str:
    recording = load_recording(connection, recording_id)
    if recording is None:
        raise ValueError(f'unknown recording: {recording_id}')
    amendment = validate_target(recording, beat, value)
    return append_edition(connection, recording_id, parent_id, amendment)

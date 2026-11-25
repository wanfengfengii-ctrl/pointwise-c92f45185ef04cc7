import sqlite3

from capture.records import load_recording
from publication.context import prepare_context
from publication.store import IssuedPacket, store_packet
from publication.window import ClosedWindow, select_window
from revision.snapshot import load_snapshot


def issue(
    connection: sqlite3.Connection, recording_id: str, edition_id: str, window: ClosedWindow
) -> IssuedPacket:
    recording = load_recording(connection, recording_id)
    if recording is None:
        raise ValueError(f'unknown recording: {recording_id}')
    snapshot = load_snapshot(connection, recording_id, edition_id)
    frames = select_window(recording.frames, window)
    context = prepare_context(recording, snapshot, window, frames)
    return store_packet(connection, recording_id, edition_id, window, frames, context.to_content())

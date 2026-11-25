import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from capture.frames import Frame, validate_label
from publication.envelope import Packet, encode_packet
from publication.window import ClosedWindow


@dataclass(frozen=True, slots=True)
class IssuedPacket:
    packet_id: str
    payload: bytes


def initialize_packets(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS issued_packets (
            packet_id TEXT PRIMARY KEY NOT NULL,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
            edition_id TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            window_end INTEGER NOT NULL CHECK (window_start <= window_end),
            payload BLOB NOT NULL
        )
    ''')


def store_packet(
    connection: sqlite3.Connection,
    recording_id: str,
    edition_id: str,
    window: ClosedWindow,
    frames: tuple[Frame, ...],
    content: dict[str, object],
) -> IssuedPacket:
    if connection.in_transaction:
        raise RuntimeError('packet issuance requires an idle connection')
    packet_id = f'packet:{uuid4().hex}'
    payload = encode_packet(Packet(packet_id, recording_id, edition_id, window, frames, content))
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''
            INSERT INTO issued_packets
                (packet_id, recording_id, edition_id, window_start, window_end, payload)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (packet_id, recording_id, edition_id, window.start, window.end, payload))
    return IssuedPacket(packet_id, payload)


def load_packet(connection: sqlite3.Connection, packet_id: str) -> IssuedPacket | None:
    validate_label(packet_id, 'packet ID')
    row = connection.execute(
        'SELECT payload FROM issued_packets WHERE packet_id = ?', (packet_id,)
    ).fetchone()
    return None if row is None else IssuedPacket(packet_id, row[0])

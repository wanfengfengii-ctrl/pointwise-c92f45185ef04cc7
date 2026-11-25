"""Primary/standby signing handoff for version 2 fixed packets.

Each station keeps its own SQLite archive. The courier only exchanges opaque
application mails; mails may be late, reordered or duplicated. Every state
transition and every produced mail is committed in the local archive so an
archive can be reopened and the handoff resumed without the source station.
"""
import hashlib
import json
import sqlite3
from dataclasses import dataclass

from capture.frames import validate_label
from publication.envelope import HandoffPacket, decode_handoff_packet

# Message kinds carried inside an application-defined mail body.
OPEN = 'open'
TAKEOVER = 'takeover'
GRANT = 'grant'
WITHDRAW = 'withdraw'

_INITIAL = 'initial'
_PENDING = 'pending'
_WITHDRAWN = 'withdrawn'
_TRANSFERRED = 'transferred'

_PRIMARY = 'primary'
_STANDBY = 'standby'

# sender role -> recipient role for each message kind
_DIRECTIONS = {
    OPEN: (_PRIMARY, _STANDBY),
    TAKEOVER: (_STANDBY, _PRIMARY),
    GRANT: (_PRIMARY, _STANDBY),
    WITHDRAW: (_PRIMARY, _STANDBY),
}


def initialize_handoff(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS handoff_station (
            station_id TEXT PRIMARY KEY NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS handoff_items (
            packet_id TEXT PRIMARY KEY NOT NULL,
            payload BLOB NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('primary', 'standby')),
            peer_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'withdrawn', 'transferred'))
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS handoff_mailbox (
            packet_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('outbox', 'inbox')),
            sender_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            body BLOB NOT NULL,
            sequence INTEGER NOT NULL,
            kind TEXT NOT NULL,
            PRIMARY KEY (packet_id, direction, body),
            UNIQUE (packet_id, direction, sequence)
        )
    ''')


    connection.execute('''
        CREATE TABLE IF NOT EXISTS handoff_reports (
            packet_id TEXT PRIMARY KEY NOT NULL,
            report_id INTEGER NOT NULL UNIQUE REFERENCES signed_reports(report_id)
        )
    ''')


def bind_station(connection: sqlite3.Connection, station_id: str) -> None:
    """Persist the local station name; reopening may rebind the same name."""
    validate_label(station_id, 'station ID')
    if connection.in_transaction:
        raise RuntimeError('station binding requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute(
            'SELECT station_id FROM handoff_station WHERE rowid = 1'
        ).fetchone()
        if row is None:
            connection.execute(
                'INSERT INTO handoff_station (rowid, station_id) VALUES (1, ?)', (station_id,)
            )
        elif row[0] != station_id:
            raise ValueError(f'archive is already bound to station {row[0]}')


def _bound_station(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        'SELECT station_id FROM handoff_station WHERE rowid = 1'
    ).fetchone()
    if row is None:
        raise ValueError('archive has no bound station')
    return row[0]


@dataclass(frozen=True, slots=True)
class _Item:
    packet_id: str
    role: str
    peer_id: str
    status: str


def _accept(payload: bytes) -> HandoffPacket:
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError('handoff payload must be V2 packet bytes')
    return decode_handoff_packet(bytes(payload))


def _role_of(packet: HandoffPacket, station_id: str) -> tuple[str, str]:
    if station_id == packet.primary_id:
        return _PRIMARY, packet.standby_id
    if station_id == packet.standby_id:
        return _STANDBY, packet.primary_id
    raise ValueError(f'station {station_id} is not a party of packet {packet.packet_id}')


def _load_item(connection: sqlite3.Connection, packet_id: str) -> _Item | None:
    row = connection.execute(
        'SELECT packet_id, role, peer_id, status FROM handoff_items WHERE packet_id = ?',
        (packet_id,),
    ).fetchone()
    return None if row is None else _Item(row[0], row[1], row[2], row[3])


def _require_item(connection: sqlite3.Connection, packet: HandoffPacket) -> _Item:
    item = _load_item(connection, packet.packet_id)
    if item is None:
        raise ValueError(f'packet {packet.packet_id} has not been opened at this station')
    return item


def _check_item_identity(item: _Item, packet: HandoffPacket, role: str, peer_id: str) -> None:
    if item.role != role or item.peer_id != peer_id:
        raise ValueError('local handoff binding does not match the packet')


def _mail_body(packet: HandoffPacket, payload: bytes, kind: str) -> bytes:
    sender_role, recipient_role = _DIRECTIONS[kind]
    sender_id = packet.primary_id if sender_role == _PRIMARY else packet.standby_id
    recipient_id = packet.standby_id if recipient_role == _STANDBY else packet.primary_id
    body = {
        'packet_id': packet.packet_id,
        'digest': hashlib.sha256(payload).hexdigest(),
        'kind': kind,
        'sender_id': sender_id,
        'recipient_id': recipient_id,
    }
    return json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _next_sequence(connection: sqlite3.Connection, packet_id: str, direction: str) -> int:
    row = connection.execute(
        'SELECT MAX(sequence) FROM handoff_mailbox WHERE packet_id = ? AND direction = ?',
        (packet_id, direction),
    ).fetchone()
    return 0 if row[0] is None else row[0] + 1


def _store_outbox(
    connection: sqlite3.Connection, packet: HandoffPacket, payload: bytes, kind: str
) -> tuple:
    body = _mail_body(packet, payload, kind)
    sender_id, recipient_id = _endpoints(packet, kind)
    existing = connection.execute(
        'SELECT sender_id, recipient_id, body FROM handoff_mailbox '
        "WHERE packet_id = ? AND direction = 'outbox' AND body = ?",
        (packet.packet_id, body),
    ).fetchone()
    if existing is not None:
        return tuple(existing)
    sequence = _next_sequence(connection, packet.packet_id, 'outbox')
    connection.execute(
        'INSERT INTO handoff_mailbox '
        '(packet_id, direction, sender_id, recipient_id, body, sequence, kind) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (packet.packet_id, 'outbox', sender_id, recipient_id, body, sequence, kind),
    )
    return sender_id, recipient_id, body


def _outbox(connection: sqlite3.Connection, packet_id: str) -> tuple[tuple, ...]:
    rows = connection.execute(
        'SELECT sender_id, recipient_id, body FROM handoff_mailbox '
        "WHERE packet_id = ? AND direction = 'outbox' ORDER BY sequence",
        (packet_id,),
    ).fetchall()
    return tuple((sender, recipient, body) for sender, recipient, body in rows)


def _endpoints(packet: HandoffPacket, kind: str) -> tuple[str, str]:
    sender_role, recipient_role = _DIRECTIONS[kind]
    sender_id = packet.primary_id if sender_role == _PRIMARY else packet.standby_id
    recipient_id = packet.standby_id if recipient_role == _STANDBY else packet.primary_id
    return sender_id, recipient_id


def _decode_mail(mail: object) -> tuple[str, str, bytes, str]:
    if not isinstance(mail, tuple) or len(mail) != 3:
        raise ValueError('mail must be a (sender_id, recipient_id, body) tuple')
    sender_id, recipient_id, body = mail
    validate_label(sender_id, 'mail sender')
    validate_label(recipient_id, 'mail recipient')
    if not isinstance(body, bytes) or not body:
        raise ValueError('mail body must be nonempty bytes')
    try:
        content = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('malformed handoff mail body') from exc
    if not isinstance(content, dict) or content.keys() != {
        'packet_id', 'digest', 'kind', 'sender_id', 'recipient_id'
    }:
        raise ValueError('handoff mail body has invalid fields')
    kind = content['kind']
    if kind not in _DIRECTIONS:
        raise ValueError('unknown handoff mail kind')
    if not isinstance(content['packet_id'], str) or not isinstance(content['digest'], str):
        raise ValueError('handoff mail references must be text')
    if content['sender_id'] != sender_id or content['recipient_id'] != recipient_id:
        raise ValueError('mail envelope does not match its body endpoints')
    return sender_id, recipient_id, body, kind


def _validate_mail_routing(
    packet: HandoffPacket, station_id: str, sender_id: str, recipient_id: str, kind: str
) -> tuple[str, str]:
    role, peer_id = _role_of(packet, station_id)
    sender_role, recipient_role = _DIRECTIONS[kind]
    expected_sender = packet.primary_id if sender_role == _PRIMARY else packet.standby_id
    expected_recipient = packet.standby_id if recipient_role == _STANDBY else packet.primary_id
    if sender_id != expected_sender or recipient_id != expected_recipient:
        raise ValueError('mail endpoints do not match its declared role')
    if recipient_id != station_id:
        raise ValueError('mail is addressed to another station')
    if sender_id != peer_id:
        raise ValueError('mail sender is not the peer station of this packet')
    return role, peer_id


def begin(connection: sqlite3.Connection, payload: bytes) -> tuple[str, tuple]:
    """The primary starts one handoff for this packet."""
    packet = _accept(payload)
    if connection.in_transaction:
        raise RuntimeError('handoff entry requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        station_id = _bound_station(connection)
        role, peer_id = _role_of(packet, station_id)
        if role != _PRIMARY:
            raise ValueError('only the primary station may begin a handoff')
        existing = _load_item(connection, packet.packet_id)
        if existing is not None:
            _check_item_identity(existing, packet, role, peer_id)
            if existing.status == _PENDING:
                return _PENDING, _outbox(connection, packet.packet_id)
            raise ValueError(
                f'handoff for packet {packet.packet_id} already reached {existing.status}'
            )
        if _packet_already_signed(connection, packet.packet_id):
            raise ValueError(f'packet {packet.packet_id} was already formally signed here')
        connection.execute(
            'INSERT INTO handoff_items (packet_id, payload, role, peer_id, status) '
            'VALUES (?, ?, ?, ?, ?)',
            (packet.packet_id, bytes(payload), role, peer_id, _PENDING),
        )
        return _PENDING, (_store_outbox(connection, packet, bytes(payload), OPEN),)


def withdraw(connection: sqlite3.Connection, payload: bytes) -> tuple[str, tuple]:
    """The primary asks to withdraw an initiated handoff."""
    packet = _accept(payload)
    if connection.in_transaction:
        raise RuntimeError('handoff entry requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        station_id = _bound_station(connection)
        role, peer_id = _role_of(packet, station_id)
        if role != _PRIMARY:
            raise ValueError('only the primary station may withdraw a handoff')
        item = _require_item(connection, packet)
        _check_item_identity(item, packet, role, peer_id)
        if item.status == _TRANSFERRED:
            raise ValueError('an effective takeover can no longer be withdrawn')
        if item.status == _WITHDRAWN:
            return _WITHDRAWN, (_outbox(connection, packet.packet_id)[-1],)
        connection.execute(
            "UPDATE handoff_items SET status = ? WHERE packet_id = ?",
            (_WITHDRAWN, packet.packet_id),
        )
        return _WITHDRAWN, (_store_outbox(connection, packet, bytes(payload), WITHDRAW),)


def takeover(connection: sqlite3.Connection, payload: bytes) -> tuple[str, tuple]:
    """The standby applies to take over an initiated handoff."""
    packet = _accept(payload)
    if connection.in_transaction:
        raise RuntimeError('handoff entry requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        station_id = _bound_station(connection)
        role, peer_id = _role_of(packet, station_id)
        if role != _STANDBY:
            raise ValueError('only the standby station may request a takeover')
        existing = _load_item(connection, packet.packet_id)
        if existing is None:
            # the courier may hand the fixed packet over before the first mail
            connection.execute(
                'INSERT INTO handoff_items (packet_id, payload, role, peer_id, status) '
                'VALUES (?, ?, ?, ?, ?)',
                (packet.packet_id, bytes(payload), role, peer_id, _PENDING),
            )
            return _PENDING, (_store_outbox(connection, packet, bytes(payload), TAKEOVER),)
        _check_item_identity(existing, packet, role, peer_id)
        if existing.status == _WITHDRAWN:
            raise ValueError('the primary has already won withdrawal for this packet')
        if existing.status == _TRANSFERRED:
            return _TRANSFERRED, (_outbox(connection, packet.packet_id)[-1],)
        return _PENDING, (_store_outbox(connection, packet, bytes(payload), TAKEOVER),)


def receive(connection: sqlite3.Connection, payload: bytes, mail: tuple) -> tuple[str, tuple]:
    """Deliver one handoff mail to this station."""
    packet = _accept(payload)
    sender_id, recipient_id, body, kind = _decode_mail(mail)
    if connection.in_transaction:
        raise RuntimeError('handoff entry requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        station_id = _bound_station(connection)
        role, peer_id = _validate_mail_routing(
            packet, station_id, sender_id, recipient_id, kind
        )
        content = json.loads(body.decode('utf-8'))
        if content['packet_id'] != packet.packet_id or content['digest'] != hashlib.sha256(
            bytes(payload)
        ).hexdigest():
            raise ValueError('mail does not reference this exact packet and its bytes')
        existing = _load_item(connection, packet.packet_id)
        if existing is not None:
            _check_item_identity(existing, packet, role, peer_id)
        duplicate = connection.execute(
            'SELECT 1 FROM handoff_mailbox '
            "WHERE packet_id = ? AND direction = 'inbox' AND body = ?",
            (packet.packet_id, body),
        ).fetchone() is not None
        sequence = _next_sequence(connection, packet.packet_id, 'inbox')
        connection.execute(
            'INSERT OR IGNORE INTO handoff_mailbox '
            '(packet_id, direction, sender_id, recipient_id, body, sequence, kind) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (packet.packet_id, 'inbox', sender_id, recipient_id, body, sequence, kind),
        )
        outgoing: tuple[tuple, ...] = ()
        if role == _PRIMARY:
            outgoing = _receive_primary(
                connection, packet, existing, kind, duplicate, bytes(payload)
            )
        else:
            _receive_standby(connection, packet, existing, kind, duplicate, bytes(payload))
        item = _load_item(connection, packet.packet_id)
        decision = _INITIAL if item is None else item.status
        return decision, outgoing


def _receive_primary(
    connection: sqlite3.Connection,
    packet: HandoffPacket,
    existing: _Item | None,
    kind: str,
    duplicate: bool,
    payload: bytes,
) -> tuple[tuple, ...]:
    if existing is None:
        raise ValueError(
            f'primary has not initiated a handoff for packet {packet.packet_id}'
        )
    if kind in (OPEN, GRANT, WITHDRAW):
        raise ValueError(f'primary cannot receive a {kind} mail for its own handoff')
    # kind == TAKEOVER
    if existing.status == _TRANSFERRED:
        if not duplicate:
            raise ValueError('a takeover arrived after the handoff was already transferred')
        return ()
    if existing.status == _WITHDRAWN:
        return ()
    connection.execute(
        "UPDATE handoff_items SET status = ? WHERE packet_id = ?",
        (_TRANSFERRED, packet.packet_id),
    )
    produced = _store_outbox(connection, packet, payload, GRANT)
    return (produced,)


def _receive_standby(
    connection: sqlite3.Connection,
    packet: HandoffPacket,
    existing: _Item | None,
    kind: str,
    duplicate: bool,
    payload: bytes,
) -> None:
    if kind == TAKEOVER:
        raise ValueError('standby cannot receive a takeover mail for its own handoff')
    if existing is None:
        # the courier may deliver any primary mail before the open mail
        initial_status = _PENDING if kind == OPEN else (
            _TRANSFERRED if kind == GRANT else _WITHDRAWN
        )
        connection.execute(
            'INSERT INTO handoff_items (packet_id, payload, role, peer_id, status) '
            'VALUES (?, ?, ?, ?, ?)',
            (packet.packet_id, payload, _STANDBY, packet.primary_id, initial_status),
        )
        return
    if kind == OPEN:
        return
    if existing.status == _PENDING:
        if kind == GRANT:
            connection.execute(
                "UPDATE handoff_items SET status = ? WHERE packet_id = ?",
                (_TRANSFERRED, packet.packet_id),
            )
        else:  # WITHDRAW
            connection.execute(
                "UPDATE handoff_items SET status = ? WHERE packet_id = ?",
                (_WITHDRAWN, packet.packet_id),
            )
        return
    if not duplicate:
        raise ValueError(
            f'a {kind} mail conflicts with the settled {existing.status} outcome'
        )


def state(connection: sqlite3.Connection, payload: bytes) -> tuple[str, tuple]:
    """Return the locally known verdict and resumable outbox mails."""
    packet = _accept(payload)
    station_id = _bound_station(connection)
    _role_of(packet, station_id)
    item = _load_item(connection, packet.packet_id)
    if item is None:
        return _INITIAL, ()
    return item.status, _outbox(connection, packet.packet_id)


def _packet_already_signed(connection: sqlite3.Connection, packet_id: str) -> bool:
    return connection.execute(
        'SELECT 1 FROM signed_reports WHERE packet_id = ?', (packet_id,)
    ).fetchone() is not None


def local_role(connection: sqlite3.Connection, packet: HandoffPacket) -> tuple[str, str]:
    """Expose the bound station's role for a packet (used by the signing entry)."""
    station_id = _bound_station(connection)
    return _role_of(packet, station_id)


def load_handoff_item(connection: sqlite3.Connection, packet_id: str) -> _Item | None:
    row = connection.execute(
        'SELECT packet_id, role, peer_id, status FROM handoff_items WHERE packet_id = ?',
        (packet_id,),
    ).fetchone()
    return None if row is None else _Item(row[0], row[1], row[2], row[3])

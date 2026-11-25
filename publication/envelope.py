import json
from dataclasses import dataclass

from capture.frames import Frame, validate_frame, validate_label
from publication.window import ClosedWindow


@dataclass(frozen=True, slots=True)
class Packet:
    packet_id: str
    recording_id: str
    edition_id: str
    window: ClosedWindow
    frames: tuple[Frame, ...]
    content: dict[str, object]


@dataclass(frozen=True, slots=True)
class HandoffPacket(Packet):
    primary_id: str
    standby_id: str


def _check_header(packet_id: str, recording_id: str, edition_id: str) -> None:
    validate_label(packet_id, 'packet ID')
    validate_label(recording_id, 'recording ID')
    validate_label(edition_id, 'edition ID')


def _check_stations(primary_id: str, standby_id: str) -> None:
    validate_label(primary_id, 'primary station ID')
    validate_label(standby_id, 'standby station ID')
    if primary_id == standby_id:
        raise ValueError('primary and standby stations must be distinct')


def _encode_frames(frames: tuple[Frame, ...]) -> list[dict[str, object]]:
    encoded = []
    for frame in frames:
        validate_frame(frame)
        encoded.append({'beat': frame.beat, 'kind': frame.kind,
                        'raw': frame.raw.hex(), 'origin': frame.origin})
    return encoded


def encode_packet(packet: Packet) -> bytes:
    _check_header(packet.packet_id, packet.recording_id, packet.edition_id)
    if not isinstance(packet.window, ClosedWindow) or not isinstance(packet.content, dict):
        raise ValueError('packet needs a closed window and content object')
    body = {
        'version': 1,
        'packet_id': packet.packet_id,
        'recording_id': packet.recording_id,
        'edition_id': packet.edition_id,
        'window': [packet.window.start, packet.window.end],
        'frames': _encode_frames(packet.frames),
        'content': packet.content,
    }
    return json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def encode_handoff_packet(packet: HandoffPacket) -> bytes:
    _check_header(packet.packet_id, packet.recording_id, packet.edition_id)
    _check_stations(packet.primary_id, packet.standby_id)
    if not isinstance(packet.window, ClosedWindow) or not isinstance(packet.content, dict):
        raise ValueError('packet needs a closed window and content object')
    body = {
        'version': 2,
        'packet_id': packet.packet_id,
        'recording_id': packet.recording_id,
        'edition_id': packet.edition_id,
        'window': [packet.window.start, packet.window.end],
        'frames': _encode_frames(packet.frames),
        'content': packet.content,
        'primary_id': packet.primary_id,
        'standby_id': packet.standby_id,
    }
    return json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    members = {}
    for key, value in pairs:
        if key in members:
            raise ValueError(f'duplicate JSON member: {key}')
        members[key] = value
    return members


def _reject_constant(value: str) -> None:
    raise ValueError(f'invalid JSON constant: {value}')


def _decode_frame(row: object) -> Frame:
    if not isinstance(row, dict) or row.keys() != {'beat', 'kind', 'raw', 'origin'}:
        raise ValueError('frame object must include beat, kind, raw and origin')
    raw = row['raw']
    if not isinstance(raw, str) or len(raw) != 2 or any(c not in '0123456789abcdefABCDEF' for c in raw):
        raise ValueError('frame raw field must be two hexadecimal digits')
    return Frame(row['beat'], row['kind'], bytes.fromhex(raw), row['origin'])


def decode_packet(payload: bytes) -> Packet:
    body = _decode_body(payload)
    fields = {'version', 'packet_id', 'recording_id', 'edition_id', 'window', 'frames', 'content'}
    if body.keys() != fields:
        raise ValueError('invalid packet envelope fields')
    if type(body['version']) is not int or body['version'] != 1:
        raise ValueError('unsupported packet version')
    return _packet_from_body(body)


def decode_handoff_packet(payload: bytes) -> HandoffPacket:
    body = _decode_body(payload)
    fields = {'version', 'packet_id', 'recording_id', 'edition_id', 'window', 'frames', 'content',
              'primary_id', 'standby_id'}
    if body.keys() != fields:
        raise ValueError('invalid handoff packet envelope fields')
    if type(body['version']) is not int or body['version'] != 2:
        raise ValueError('unsupported handoff packet version')
    packet = _packet_from_body(body)
    primary_id, standby_id = body['primary_id'], body['standby_id']
    _check_stations(primary_id, standby_id)
    return HandoffPacket(
        packet.packet_id, packet.recording_id, packet.edition_id, packet.window,
        packet.frames, packet.content, primary_id, standby_id,
    )


def _decode_body(payload: bytes) -> dict[str, object]:
    if not isinstance(payload, bytes):
        raise ValueError('packet payload must be UTF-8 JSON bytes')
    try:
        body = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_members,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid packet JSON') from exc
    if not isinstance(body, dict):
        raise ValueError('invalid packet envelope fields')
    return body


def _packet_from_body(body: dict[str, object]) -> Packet:
    _check_header(body['packet_id'], body['recording_id'], body['edition_id'])
    window = body['window']
    if not isinstance(window, list) or len(window) != 2:
        raise ValueError('packet window must contain two endpoints')
    if not isinstance(body['frames'], list) or not isinstance(body['content'], dict):
        raise ValueError('packet needs a frame list and content object')
    return Packet(
        body['packet_id'], body['recording_id'], body['edition_id'], ClosedWindow(*window),
        tuple(_decode_frame(row) for row in body['frames']), body['content'],
    )

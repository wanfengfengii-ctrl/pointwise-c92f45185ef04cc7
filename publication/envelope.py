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


def _check_header(packet_id: str, recording_id: str, edition_id: str) -> None:
    validate_label(packet_id, 'packet ID')
    validate_label(recording_id, 'recording ID')
    validate_label(edition_id, 'edition ID')


def encode_packet(packet: Packet) -> bytes:
    _check_header(packet.packet_id, packet.recording_id, packet.edition_id)
    if not isinstance(packet.window, ClosedWindow) or not isinstance(packet.content, dict):
        raise ValueError('packet needs a closed window and content object')
    frames = []
    for frame in packet.frames:
        validate_frame(frame)
        frames.append({'beat': frame.beat, 'kind': frame.kind,
                       'raw': frame.raw.hex(), 'origin': frame.origin})
    body = {
        'version': 1,
        'packet_id': packet.packet_id,
        'recording_id': packet.recording_id,
        'edition_id': packet.edition_id,
        'window': [packet.window.start, packet.window.end],
        'frames': frames,
        'content': packet.content,
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
    if not isinstance(payload, bytes):
        raise ValueError('packet payload must be UTF-8 JSON bytes')
    try:
        body = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_members,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid packet JSON') from exc
    fields = {'version', 'packet_id', 'recording_id', 'edition_id', 'window', 'frames', 'content'}
    if not isinstance(body, dict) or body.keys() != fields:
        raise ValueError('invalid packet envelope fields')
    if type(body['version']) is not int or body['version'] != 1:
        raise ValueError('unsupported packet version')
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

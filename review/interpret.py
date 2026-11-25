from dataclasses import dataclass

from capture.decoder import advance_level
from capture.frames import validate_beat, validate_level
from review.intake import AcceptedPacket


@dataclass(frozen=True, slots=True)
class ReviewReading:
    beat: int
    value: int
    origin: str


def _read_content(accepted: AcceptedPacket) -> tuple[int | None, dict[int, int]]:
    packet = accepted.packet
    if packet.content.keys() - {'previous_level', 'amendments'}:
        raise ValueError('unknown packet interpretation material')
    previous_level = None
    if 'previous_level' in packet.content:
        previous_level = packet.content['previous_level']
        validate_level(previous_level)
    if packet.frames[0].kind == 'delta' and previous_level is None:
        raise ValueError('delta window requires the previous original level')

    rows = packet.content.get('amendments', [])
    if not isinstance(rows, list):
        raise ValueError('amendments must be a list of beat and level pairs')
    frame_beats = {frame.beat for frame in packet.frames}
    amendments = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError('invalid amendment pair')
        beat, value = row
        validate_beat(beat)
        validate_level(value)
        if beat not in frame_beats or beat in amendments:
            raise ValueError(f'amendment target {beat} must occur once in the window')
        amendments[beat] = value
    return previous_level, amendments


def interpret_packet(accepted: AcceptedPacket) -> tuple[ReviewReading, ...]:
    previous_level, amendments = _read_content(accepted)
    readings = []
    for frame in accepted.packet.frames:
        previous_level = advance_level(frame, previous_level)
        value = amendments.get(frame.beat, previous_level)
        readings.append(ReviewReading(frame.beat, value, frame.origin))
    return tuple(readings)

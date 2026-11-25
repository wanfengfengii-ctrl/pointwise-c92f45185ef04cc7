from dataclasses import dataclass
from typing import Iterable

from capture.frames import Frame, validate_frame_order


@dataclass(frozen=True, slots=True)
class OriginalReading:
    beat: int
    value: int
    origin: str


def clamp_level(value: int) -> int:
    return min(255, max(0, value))


def advance_level(frame: Frame, previous_level: int | None) -> int:
    """Advance the original decoder by one already validated frame."""
    if frame.kind == 'absolute':
        return frame.operand
    if previous_level is None:
        raise ValueError(f'delta at beat {frame.beat} lacks an original level')
    return clamp_level(previous_level + frame.operand)


def decode_recording(frames: Iterable[Frame]) -> tuple[OriginalReading, ...]:
    frames = tuple(frames)
    validate_frame_order(frames)
    if frames[0].kind != 'absolute':
        raise ValueError('an original recording must start with an absolute frame')
    previous_level = None
    readings = []
    for frame in frames:
        previous_level = advance_level(frame, previous_level)
        readings.append(OriginalReading(frame.beat, previous_level, frame.origin))
    return tuple(readings)

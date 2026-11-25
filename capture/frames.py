from dataclasses import dataclass
from typing import Iterable


MAX_BEAT = (1 << 63) - 1


@dataclass(frozen=True, slots=True)
class Frame:
    beat: int
    kind: str
    raw: bytes
    origin: str

    @property
    def operand(self) -> int:
        return int.from_bytes(self.raw, 'big', signed=self.kind == 'delta')


def validate_label(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be nonempty text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{field} must be valid Unicode text') from exc


def validate_beat(beat: int) -> None:
    if type(beat) is not int or not 1 <= beat <= MAX_BEAT:
        raise ValueError('beat must be a positive SQLite-range integer')


def validate_level(value: int) -> None:
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError('level must be an integer from 0 to 255')


def validate_frame(frame: Frame) -> None:
    if not isinstance(frame, Frame):
        raise ValueError('a frame must include beat, kind, raw byte and origin')
    validate_beat(frame.beat)
    if frame.kind not in ('absolute', 'delta'):
        raise ValueError(f'unknown frame kind at beat {frame.beat}')
    if not isinstance(frame.raw, bytes) or len(frame.raw) != 1:
        raise ValueError(f'expected one raw byte at beat {frame.beat}')
    validate_label(frame.origin, 'frame origin')


def validate_frame_order(frames: Iterable[Frame]) -> None:
    previous_beat = 0
    for frame in frames:
        validate_frame(frame)
        if frame.beat <= previous_beat:
            raise ValueError(f'beats must be strictly increasing at {frame.beat}')
        previous_beat = frame.beat
    if previous_beat == 0:
        raise ValueError('at least one original frame is required')

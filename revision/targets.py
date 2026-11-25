from dataclasses import dataclass

from capture.frames import validate_beat, validate_level
from capture.records import Recording


@dataclass(frozen=True, slots=True)
class Amendment:
    beat: int
    value: int


def validate_target(recording: Recording, beat: int, value: int) -> Amendment:
    validate_beat(beat)
    validate_level(value)
    if not any(frame.beat == beat for frame in recording.frames):
        raise ValueError(f'beat {beat} is absent from recording {recording.recording_id}')
    return Amendment(beat, value)

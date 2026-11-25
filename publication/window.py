from dataclasses import dataclass

from capture.frames import Frame, validate_beat


@dataclass(frozen=True, slots=True)
class ClosedWindow:
    start: int
    end: int

    def __post_init__(self) -> None:
        validate_beat(self.start)
        validate_beat(self.end)
        if self.start > self.end:
            raise ValueError('window start is after its end')

    def contains(self, beat: int) -> bool:
        return self.start <= beat <= self.end


def select_window(frames: tuple[Frame, ...], window: ClosedWindow) -> tuple[Frame, ...]:
    if not isinstance(window, ClosedWindow):
        raise ValueError('issue requires a closed window')
    if not frames or window.start < frames[0].beat or window.end > frames[-1].beat:
        raise ValueError('closed window is outside the recording')
    selected = tuple(frame for frame in frames if window.contains(frame.beat))
    if not selected:
        raise ValueError('closed window contains no recorded beat')
    return selected

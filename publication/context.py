from dataclasses import dataclass

from capture.decoder import decode_recording
from capture.frames import Frame
from capture.records import Recording
from publication.window import ClosedWindow
from revision.snapshot import EditionSnapshot


@dataclass(frozen=True, slots=True)
class WindowContext:
    previous_level: int | None
    amendments: tuple[tuple[int, int], ...]

    def to_content(self) -> dict[str, object]:
        content: dict[str, object] = {'amendments': [list(pair) for pair in self.amendments]}
        if self.previous_level is not None:
            content['previous_level'] = self.previous_level
        return content


def prepare_context(
    recording: Recording,
    snapshot: EditionSnapshot,
    window: ClosedWindow,
    selected: tuple[Frame, ...],
) -> WindowContext:
    if snapshot.recording_id != recording.recording_id:
        raise ValueError('edition snapshot and recording do not match')
    if not selected or any(not window.contains(frame.beat) for frame in selected):
        raise ValueError('window context requires its selected original frames')
    first_index = next(
        (index for index, frame in enumerate(recording.frames) if frame.beat == selected[0].beat),
        None,
    )
    if first_index is None or recording.frames[first_index:first_index + len(selected)] != selected:
        raise ValueError('selected frames must form an original recording segment')

    previous_level = None
    if selected[0].kind == 'delta':
        if first_index == 0:
            raise ValueError('the original material lacks a level before the window')
        previous_level = decode_recording(recording.frames[:first_index])[-1].value
    amendments = tuple(
        (frame.beat, snapshot.amendments[frame.beat])
        for frame in selected
        if frame.beat in snapshot.amendments
    )
    return WindowContext(previous_level, amendments)

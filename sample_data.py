import json
from pathlib import Path

from capture.frames import Frame


SAMPLE_DIRECTORY = Path(__file__).resolve().parent / 'samples'


def load_sample_recording() -> tuple[str, tuple[Frame, ...]]:
    document = json.loads((SAMPLE_DIRECTORY / 'recording.json').read_text(encoding='utf-8'))
    frames = tuple(
        Frame(row['beat'], row['kind'], bytes.fromhex(row['raw']), row['origin'])
        for row in document['frames']
    )
    return document['recording_id'], frames


def load_legacy_packet() -> bytes:
    return (SAMPLE_DIRECTORY / 'legacy_packet.json').read_bytes()

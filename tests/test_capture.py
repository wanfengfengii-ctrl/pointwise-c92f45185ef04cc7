import sqlite3
from dataclasses import replace

from capture.frames import Frame
from capture.intake import ingest
from capture.records import load_recording
from sample_data import load_sample_recording
from tests.fixtures import LabCase, ORIGIN_A, ORIGIN_B, ORIGIN_C, ORIGIN_D, ORIGIN_E, emit_scenario


class CaptureTests(LabCase):
    def test_limit_and_reset(self):
        with self.source() as source:
            recording = self.ingest_sample(source)
        with self.source() as source:
            stored = load_recording(source, recording.recording_id)
        expected = {
            'rows': [[10, 250, ORIGIN_A], [20, 255, ORIGIN_B], [40, 252, ORIGIN_C],
                     [50, 10, ORIGIN_D], [70, 0, ORIGIN_E]],
            'total': 767,
            'saturated': [[20, ORIGIN_B]],
        }
        actual = {
            'rows': [[row.beat, row.value, row.origin] for row in recording.ledger.readings],
            'total': recording.ledger.total,
            'saturated': [[row.beat, row.origin] for row in recording.ledger.saturated],
        }
        self.assertEqual(actual, expected)
        self.assertEqual(stored, recording)
        self.assertEqual(stored.frames[1].raw, b'\x0a')
        persisted = {'total': stored.ledger.total, 'saturated': [
            [row.beat, row.origin] for row in stored.ledger.saturated
        ]}
        emit_scenario(
            'T1.original_ledger',
            {'beats': [frame.beat for frame in recording.frames],
             'raw': [frame.raw.hex() for frame in recording.frames]},
            expected, actual, {'total': 767, 'saturated': [[20, ORIGIN_B]]}, persisted,
            ['capture/intake.py', 'capture/decoder.py', 'capture/ledger.py', 'capture/records.py'],
            '250+10 clamps to 255, then 255-3 is 252. Absolute 10 resets and 10-20 clamps to 0.',
        )

    def test_missing_initial_absolute(self):
        with self.source() as source:
            existing = self.ingest_sample(source)
            with self.assertRaisesRegex(ValueError, 'must start with an absolute'):
                ingest(source, 'delta-first', existing.frames[1:])
            self.assertIsNone(load_recording(source, 'delta-first'))
            self.assertEqual(load_recording(source, existing.recording_id), existing)

    def test_late_invalid_order(self):
        _, frames = load_sample_recording()
        ordered = (frames[0], replace(frames[1], beat=30), frames[2])
        reversed_tail = (ordered[0], ordered[2], ordered[1])
        with self.source() as source:
            accepted = ingest(source, 'ordered', ordered)
            with self.assertRaisesRegex(ValueError, 'strictly increasing'):
                ingest(source, 'late-inversion', reversed_tail)
        with self.source() as source:
            self.assertEqual(load_recording(source, 'ordered'), accepted)
            self.assertIsNone(load_recording(source, 'late-inversion'))

    def test_invalid_frame_or_duplicate_recording_does_not_overwrite(self):
        with self.source() as source:
            accepted = self.ingest_sample(source)
            first = accepted.frames[0]
            invalid = (
                (),
                (replace(first, raw=b''),),
                (replace(first, raw=b'\xfa\x00'),),
                (replace(first, kind='relative'),),
                (replace(first, origin=' '),),
                (replace(first, beat=True),),
                (first, Frame(10, 'delta', b'\x01', 'duplicate')),
                ({'beat': 10, 'kind': 'absolute'},),
            )
            for candidate in invalid:
                with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                    ingest(source, 'invalid', candidate)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                ingest(source, accepted.recording_id, (replace(first, raw=b'\x00'),))
            self.assertIsNone(load_recording(source, 'invalid'))
            self.assertEqual(load_recording(source, accepted.recording_id), accepted)

    def test_database_failure_rolls_back_recording_header_and_frames(self):
        _, frames = load_sample_recording()
        with self.source() as source:
            source.execute('''
                CREATE TRIGGER refuse_last_frame BEFORE INSERT ON recording_frames
                WHEN NEW.beat = 70
                BEGIN SELECT RAISE(ABORT, 'frame storage refused'); END
            ''')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'frame storage refused'):
                ingest(source, 'storage-failure', frames)
        with self.source() as source:
            self.assertIsNone(load_recording(source, 'storage-failure'))
            self.assertEqual(source.execute('SELECT COUNT(*) FROM recording_frames').fetchone()[0], 0)

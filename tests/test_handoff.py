import sqlite3

from publication.envelope import decode_packet
from publication.issue import issue
from publication.store import load_packet
from publication.window import ClosedWindow
from review.intake import accept_packet
from review.sign import review
from revision.amend import amend
from revision.history import original_edition_id
from sample_data import load_legacy_packet
from tests.fixtures import (
    LabCase, ORIGIN_A, ORIGIN_B, ORIGIN_C, ORIGIN_D, ORIGIN_E, archive_counts, detail_rows,
    emit_scenario, independent_payload,
)


RAW_A = (10, 'absolute', 'fa', ORIGIN_A)
RAW_B = (20, 'delta', '0a', ORIGIN_B)
RAW_C = (40, 'delta', 'fd', ORIGIN_C)
SHORT_ROWS = [[20, 255, ORIGIN_B], [40, 252, ORIGIN_C]]
LONG_ROWS = [[10, 240, ORIGIN_A], [20, 255, ORIGIN_B], [40, 252, ORIGIN_C]]


class HandoffTests(LabCase):
    def test_short_and_long_windows(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            edition = amend(source, key, original_edition_id(key), 10, 240)
            short = issue(source, key, edition, ClosedWindow(20, 40))
            long = issue(source, key, edition, ClosedWindow(10, 40))
            persisted = load_packet(source, short.packet_id)
            self.assertEqual(persisted, short)
        self.source_path.unlink()
        with self.archive() as archive:
            short_report = review(archive, persisted.payload)
            long_report = review(archive, long.payload)
        self.assertFalse(self.source_path.exists())
        self.assertEqual(detail_rows(short_report), SHORT_ROWS, 'BC_LEVEL_DRIFT')
        self.assertEqual(detail_rows(long_report), LONG_ROWS, 'ABC_LEVEL_DRIFT')
        self.assertEqual(detail_rows(short_report), detail_rows(long_report)[1:])
        short_content = short_report.report.content
        long_content = long_report.report.content
        self.assertEqual(short_content.total, 507)
        self.assertEqual(long_content.total, 747)
        self.assertEqual(short_content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(long_content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(short_content.edition_id, edition)
        self.assertEqual(short_content.packet_id, short.packet_id)
        self.assertEqual(short_report.receipt.packet_id, short.packet_id)
        wire = decode_packet(short.payload)
        self.assertEqual([(frame.beat, frame.raw.hex(), frame.origin) for frame in wire.frames],
                         [(20, '0a', ORIGIN_B), (40, 'fd', ORIGIN_C)])
        self.assertEqual(wire.content.get('amendments'), [])
        self.assertNotIn(ORIGIN_A.encode(), short.payload)
        emit_scenario(
            'T4.delta_start_origin', {'window': [20, 40], 'recorded_operands': [250, 10, -3],
                                     'amendment': [10, 240]}, SHORT_ROWS, detail_rows(short_report),
            {'total': 507, 'saturated': [[20, ORIGIN_B]], 'parent_matches': True},
            {'total': short_content.total, 'saturated': short_content.saturated,
             'parent_matches': short_report.receipt.packet_id == short.packet_id},
            ['publication/issue.py', 'publication/context.py', 'review/interpret.py', 'review/sign.py'],
            'The original 250 plus 10 first clamps to 255, then subtracting 3 gives 252.',
        )

    def test_independent_long(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            edition = amend(source, key, original_edition_id(key), 10, 240)
            short = issue(source, key, edition, ClosedWindow(20, 40))
        self.source_path.unlink()
        long_payload = independent_payload(
            packet_id='independent-abc', edition_id=edition, window=(10, 40),
            frames=(RAW_A, RAW_B, RAW_C), content={'amendments': [[10, 240]]},
        )
        with self.archive() as archive:
            long_report = review(archive, long_payload)
            short_report = review(archive, short.payload)
        content = long_report.report.content
        self.assertEqual(detail_rows(long_report), LONG_ROWS, 'ABC_LEVEL_DRIFT')
        self.assertEqual(content.total, 747)
        self.assertEqual(content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(detail_rows(short_report), SHORT_ROWS)
        self.assertEqual(detail_rows(long_report)[1:], detail_rows(short_report))
        self.assertEqual(long_report.receipt.packet_id, 'independent-abc')
        emit_scenario(
            'T4.independent_long', {'window': [10, 40], 'frames': [RAW_A, RAW_B, RAW_C],
                                   'amendments': [[10, 240]]}, LONG_ROWS, detail_rows(long_report),
            {'total': 747, 'saturated': [[20, ORIGIN_B]], 'overlap_matches': True},
            {'total': content.total, 'saturated': content.saturated,
             'overlap_matches': detail_rows(long_report)[1:] == detail_rows(short_report)},
            ['review/intake.py', 'review/interpret.py', 'review/tally.py', 'review/sign.py'],
            'Replace the displayed value of A only, using the manually clamped original B and C.',
        )

    def test_closed_endpoint(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            packet = issue(source, key, original_edition_id(key), ClosedWindow(10, 20))
            inside_gap = issue(source, key, original_edition_id(key), ClosedWindow(11, 39))
        wire = decode_packet(packet.payload)
        self.assertEqual([(frame.beat, frame.raw.hex(), frame.origin) for frame in wire.frames],
                         [(10, 'fa', ORIGIN_A), (20, '0a', ORIGIN_B)])
        with self.archive() as archive:
            archived = review(archive, packet.payload)
            gap_report = review(archive, inside_gap.payload)
        self.assertEqual(detail_rows(archived), [[10, 250, ORIGIN_A], [20, 255, ORIGIN_B]])
        self.assertEqual(archived.report.content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(detail_rows(gap_report), [[20, 255, ORIGIN_B]])

    def test_negative_start_and_reset(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            edition = amend(source, key, original_edition_id(key), 10, 240)
            packet = issue(source, key, edition, ClosedWindow(40, 70))
        self.source_path.unlink()
        with self.archive() as archive:
            archived = review(archive, packet.payload)
        self.assertEqual(detail_rows(archived), [[40, 252, ORIGIN_C], [50, 10, ORIGIN_D], [70, 0, ORIGIN_E]])
        self.assertEqual(archived.report.content.total, 262)
        self.assertEqual(archived.report.content.saturated, ())

    def test_saturation_identity(self):
        legacy_absolute = independent_payload(
            packet_id='independent-ab', edition_id='original:rig07-run004', window=(10, 20),
            frames=(RAW_A, RAW_B), content={},
        )
        zero = independent_payload(
            packet_id='independent-zero', edition_id='original:rig07-run004', window=(50, 70),
            frames=((50, 'absolute', '0a', ORIGIN_D), (70, 'delta', 'ec', ORIGIN_E)), content={},
        )
        with self.archive() as archive:
            saturated = review(archive, legacy_absolute)
            unsaturated = review(archive, zero)
        self.assertEqual(detail_rows(saturated), [[10, 250, ORIGIN_A], [20, 255, ORIGIN_B]])
        self.assertEqual(saturated.report.content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(unsaturated.report.content.saturated, ())
        self.assertEqual(detail_rows(unsaturated), [[50, 10, ORIGIN_D], [70, 0, ORIGIN_E]])

    def test_legacy_packet(self):
        with self.archive() as archive:
            archived = review(archive, load_legacy_packet())
        self.assertFalse(self.source_path.exists())
        self.assertEqual(detail_rows(archived), SHORT_ROWS)
        self.assertEqual(archived.report.content.total, 507)
        self.assertEqual(archived.report.content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(archived.receipt.packet_id, 'legacy-rig07-004-bc')

    def test_window_out_of_range(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            root = original_edition_id(key)
            existing = issue(source, key, root, ClosedWindow(20, 40))
            for start, end in ((10, 71), (9, 20), (40, 20), (21, 39)):
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    issue(source, key, root, ClosedWindow(start, end))
            with self.assertRaises(ValueError):
                issue(source, key, 'unknown-edition', ClosedWindow(20, 40))
            self.assertEqual(source.execute('SELECT COUNT(*) FROM issued_packets').fetchone()[0], 1)
            self.assertEqual(load_packet(source, existing.packet_id), existing)

    def test_failed_packet_store_preserves_existing_packet(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            root = original_edition_id(key)
            existing = issue(source, key, root, ClosedWindow(10, 20))
            source.execute('''
                CREATE TRIGGER refuse_new_packet BEFORE INSERT ON issued_packets
                BEGIN SELECT RAISE(ABORT, 'packet storage refused'); END
            ''')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'packet storage refused'):
                issue(source, key, root, ClosedWindow(20, 40))
        with self.source() as source:
            self.assertEqual(load_packet(source, existing.packet_id), existing)
            self.assertEqual(source.execute('SELECT COUNT(*) FROM issued_packets').fetchone()[0], 1)

    def test_missing_initial_context(self):
        payload = independent_payload(
            packet_id='missing-context', edition_id='original:rig07-run004', window=(20, 40),
            frames=(RAW_B, RAW_C), content={'amendments': []},
        )
        self.assertEqual(accept_packet(payload).packet.packet_id, 'missing-context')
        with self.archive() as archive:
            with self.assertRaisesRegex(ValueError, 'previous original level'):
                review(archive, payload)
        with self.archive() as archive:
            self.assertEqual(archive_counts(archive), {'reports': 0, 'details': 0, 'receipts': 0})

    def test_invalid_frames_and_amendments_do_not_sign(self):
        cases = (
            ((RAW_A, RAW_C, RAW_B), {}),
            ((RAW_A, RAW_B, RAW_B), {}),
            ((RAW_A, (50, 'delta', 'fd', ORIGIN_C)), {}),
            ((RAW_A, (20, 'delta', '0', ORIGIN_B)), {}),
            ((RAW_A, (20, 'delta', '0a', '')), {}),
            ((RAW_A, RAW_B), {'amendments': [[40, 10]]}),
            ((RAW_A, RAW_B), {'amendments': [[10, 0], [10, 1]]}),
            ((RAW_A, RAW_B), {'amendments': [[10, True]]}),
            ((RAW_B, RAW_C), {'previous_level': -1}),
        )
        with self.archive() as archive:
            for frames, content in cases:
                payload = independent_payload(
                    packet_id='invalid', edition_id='original:rig07-run004', window=(10, 40),
                    frames=frames, content=content,
                )
                with self.subTest(frames=frames, content=content), self.assertRaises(ValueError):
                    review(archive, payload)
            self.assertEqual(archive_counts(archive), {'reports': 0, 'details': 0, 'receipts': 0})

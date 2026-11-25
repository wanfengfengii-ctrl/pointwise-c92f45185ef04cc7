import sqlite3

from archive.lookup import read
from publication.issue import issue
from publication.window import ClosedWindow
from review.sign import review
from revision.amend import amend
from revision.history import original_edition_id
from tests.fixtures import LabCase, ORIGIN_A, ORIGIN_B, ORIGIN_C, archive_counts, detail_rows, emit_scenario


class ArchiveTests(LabCase):
    def test_failed_sign(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            edition = original_edition_id(key)
            first = issue(source, key, edition, ClosedWindow(10, 20))
            second = issue(source, key, edition, ClosedWindow(20, 40))
        self.assertNotEqual(first.packet_id, second.packet_id)
        with self.archive() as archive:
            old = review(archive, first.payload)
            archive.execute('''
                CREATE TRIGGER reject_receipt_after_report BEFORE INSERT ON success_receipts
                WHEN EXISTS (SELECT 1 FROM signed_reports WHERE report_id = NEW.report_id)
                BEGIN SELECT RAISE(ABORT, 'receipt refused after report write'); END
            ''')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt refused after report write'):
                review(archive, second.payload)
            self.assertFalse(archive.in_transaction)
        expected_after_failure = {'reports': 1, 'details': 2, 'receipts': 1}
        with self.archive() as archive:
            after_failure = archive_counts(archive)
            self.assertEqual(after_failure, expected_after_failure)
            self.assertEqual(read(archive, old.report.report_id), old)
            archive.execute('DROP TRIGGER reject_receipt_after_report')
            new = review(archive, second.payload)
        with self.archive() as archive:
            self.assertEqual(archive_counts(archive), {'reports': 2, 'details': 4, 'receipts': 2})
            self.assertEqual(read(archive, old.report.report_id), old)
            self.assertEqual(read(archive, new.report.report_id), new)
        self.assertEqual(detail_rows(new), [[20, 255, ORIGIN_B], [40, 252, ORIGIN_C]])
        self.assertEqual(new.report.content.total, 507)
        for archived, packet in ((old, first), (new, second)):
            self.assertEqual(archived.report.content.edition_id, edition)
            self.assertEqual(archived.report.content.packet_id, packet.packet_id)
            self.assertEqual(archived.receipt.packet_id, packet.packet_id)
            self.assertEqual(archived.receipt.report_id, archived.report.report_id)
        self.assertEqual(new.report.content.saturated, ((20, ORIGIN_B),))
        emit_scenario(
            'T7.atomic_retry', {'old_window': [10, 20], 'new_window': [20, 40],
                                'failure_point': 'receipt insert after report write'},
            expected_after_failure, after_failure,
            {'retry_total': 507, 'old_parent_matches': True, 'new_parent_matches': True},
            {'retry_total': new.report.content.total,
             'old_parent_matches': old.receipt.packet_id == first.packet_id,
             'new_parent_matches': new.receipt.packet_id == second.packet_id},
            ['review/sign.py', 'archive/commit.py', 'archive/reports.py', 'archive/receipts.py'],
            'The failed transaction leaves the one preexisting report, its two details and one receipt.',
        )

    def test_deferred_commit_failure(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            packet = issue(source, key, original_edition_id(key), ClosedWindow(20, 40))
        with self.archive() as archive:
            archive.execute('''
                CREATE TABLE commit_guard (
                    report_id INTEGER REFERENCES signed_reports(report_id) DEFERRABLE INITIALLY DEFERRED
                )
            ''')
            archive.execute('''
                CREATE TRIGGER invalidate_commit AFTER INSERT ON success_receipts
                BEGIN INSERT INTO commit_guard VALUES (-NEW.report_id); END
            ''')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
                review(archive, packet.payload)
            self.assertFalse(archive.in_transaction)
        with self.archive() as archive:
            self.assertEqual(archive_counts(archive), {'reports': 0, 'details': 0, 'receipts': 0})
            self.assertEqual(archive.execute('SELECT COUNT(*) FROM commit_guard').fetchone()[0], 0)
            archive.execute('DROP TRIGGER invalidate_commit')
            self.assertEqual(review(archive, packet.payload).report.content.total, 507)

    def test_old_report_after_new_edition(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            root = original_edition_id(key)
            old_packet = issue(source, key, root, ClosedWindow(10, 40))
        with self.archive() as archive:
            old = review(archive, old_packet.payload)
        with self.source() as source:
            next_edition = amend(source, key, root, 10, 240)
            new_packet = issue(source, key, next_edition, ClosedWindow(10, 40))
        self.source_path.unlink()
        with self.archive() as archive:
            new = review(archive, new_packet.payload)
            self.assertEqual(new.report.content.total, 747)
        with self.archive() as archive:
            retrieved = read(archive, old.report.report_id)
            self.assertEqual(read(archive, new.report.report_id), new)
            self.assertIsNone(read(archive, new.report.report_id + 100))
        expected = [[10, 250, ORIGIN_A], [20, 255, ORIGIN_B], [40, 252, ORIGIN_C]]
        self.assertEqual(retrieved, old)
        self.assertEqual(detail_rows(retrieved), expected)
        self.assertEqual(retrieved.report.content.total, 757)
        self.assertEqual(retrieved.report.content.saturated, ((20, ORIGIN_B),))
        self.assertEqual(retrieved.report.content.edition_id, root)
        self.assertEqual(retrieved.report.content.packet_id, old_packet.packet_id)
        self.assertEqual(retrieved.receipt, old.receipt)
        emit_scenario(
            'T8.frozen_order', {'original_window': [10, 40], 'later_amendment': [10, 240]},
            expected, detail_rows(retrieved),
            {'total': 757, 'saturated': [[20, ORIGIN_B]], 'receipt_matches': True},
            {'total': retrieved.report.content.total, 'saturated': retrieved.report.content.saturated,
             'receipt_matches': retrieved.receipt == old.receipt},
            ['archive/lookup.py', 'archive/reports.py', 'archive/receipts.py', 'archive/reconcile.py'],
            'The original report retains the A, B, C device order and the sum 250+255+252.',
        )

    def test_lookup_rejects_inconsistent_receipt(self):
        with self.source() as source:
            key = self.ingest_sample(source).recording_id
            packet = issue(source, key, original_edition_id(key), ClosedWindow(10, 20))
        with self.archive() as archive:
            archived = review(archive, packet.payload)
            with archive:
                archive.execute('UPDATE success_receipts SET packet_id = ? WHERE report_id = ?',
                                ('unrelated-packet', archived.report.report_id))
        with self.archive() as archive:
            with self.assertRaisesRegex(ValueError, 'receipt binding'):
                read(archive, archived.report.report_id)

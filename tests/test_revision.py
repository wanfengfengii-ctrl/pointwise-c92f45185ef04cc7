from capture.records import load_recording
from publication.issue import issue
from publication.window import ClosedWindow
from review.sign import review
from revision.amend import amend
from revision.history import load_edition_link, original_edition_id
from revision.snapshot import load_snapshot
from tests.fixtures import LabCase, ORIGIN_A, ORIGIN_B, ORIGIN_C, detail_rows


class RevisionTests(LabCase):
    def test_reopen_preserves_amendment(self):
        with self.source() as source:
            recording = self.ingest_sample(source)
            root = original_edition_id(recording.recording_id)
            edition = amend(source, recording.recording_id, root, 10, 240)
        with self.source() as source:
            stored = load_recording(source, recording.recording_id)
            snapshot = load_snapshot(source, recording.recording_id, edition)
            packet = issue(source, recording.recording_id, edition, ClosedWindow(10, 10))
        self.assertEqual(stored, recording)
        self.assertEqual(stored.frames[0].raw, b'\xfa')
        self.assertEqual(stored.ledger.readings[0].value, 250)
        self.assertEqual(dict(snapshot.amendments), {10: 240})
        with self.archive() as archive:
            self.assertEqual(detail_rows(review(archive, packet.payload)), [[10, 240, ORIGIN_A]])

    def test_explicit_ancestry(self):
        with self.source() as source:
            recording = self.ingest_sample(source)
            key = recording.recording_id
            root = original_edition_id(key)
            first = amend(source, key, root, 10, 240)
            second = amend(source, key, first, 10, 230)
            branch = amend(source, key, root, 20, 0)
        with self.source() as source:
            for edition, expected in ((root, {}), (first, {10: 240}),
                                      (second, {10: 230}), (branch, {20: 0})):
                with self.subTest(edition=edition):
                    snapshot = load_snapshot(source, key, edition)
                    self.assertEqual(snapshot.edition_id, edition)
                    self.assertEqual(dict(snapshot.amendments), expected)
            self.assertEqual(load_edition_link(source, second).parent_id, first)
            self.assertEqual(load_recording(source, key).ledger.readings[0].value, 250)
            packet = issue(source, key, branch, ClosedWindow(10, 40))
        with self.archive() as archive:
            archived = review(archive, packet.payload)
        self.assertEqual(detail_rows(archived), [[10, 250, ORIGIN_A], [20, 0, ORIGIN_B], [40, 252, ORIGIN_C]])
        self.assertEqual(archived.report.content.total, 502)
        self.assertEqual(archived.report.content.saturated, ())

    def test_invalid_amendment(self):
        from capture.intake import ingest

        with self.source() as source:
            recording = self.ingest_sample(source)
            key = recording.recording_id
            root = original_edition_id(key)
            good = amend(source, key, root, 10, 240)
            other = ingest(source, 'other-recording', recording.frames)
            other_root = original_edition_id(other.recording_id)
            other_edition = amend(source, other.recording_id, other_root, 10, 245)
            before = source.execute('SELECT COUNT(*) FROM edition_links').fetchone()[0]
            invalid = ((root, 30, 240), ('absent', 10, 240), (other_root, 10, 240),
                       (other_edition, 10, 240), (root, 10, -1), (root, 10, 256), (root, 10, True))
            for parent, beat, level in invalid:
                with self.subTest(parent=parent, beat=beat, level=level), self.assertRaises(ValueError):
                    amend(source, key, parent, beat, level)
            with self.assertRaises(ValueError):
                amend(source, 'absent-recording', root, 10, 240)
            self.assertEqual(source.execute('SELECT COUNT(*) FROM edition_links').fetchone()[0], before)
            self.assertEqual(dict(load_snapshot(source, key, good).amendments), {10: 240})
            self.assertEqual(load_recording(source, key), recording)

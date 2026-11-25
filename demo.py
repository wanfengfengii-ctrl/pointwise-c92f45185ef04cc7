import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.commit import initialize_archive
from archive.lookup import read
from capture.intake import ingest
from capture.records import initialize_recordings
from publication.issue import issue
from publication.store import initialize_packets
from publication.window import ClosedWindow
from review.sign import review
from revision.amend import amend
from revision.history import initialize_editions, original_edition_id
from sample_data import load_sample_recording


def main() -> None:
    recording_id, frames = load_sample_recording()
    original_id = original_edition_id(recording_id)
    with TemporaryDirectory(prefix='strain-handoff-') as directory:
        source_path = Path(directory) / 'source.sqlite'
        archive_path = Path(directory) / 'archive.sqlite'
        with closing(sqlite3.connect(source_path)) as source:
            initialize_recordings(source)
            initialize_editions(source)
            initialize_packets(source)
            original = ingest(source, recording_id, frames)
            before = issue(source, recording_id, original_id, ClosedWindow(10, 40))
        with closing(sqlite3.connect(archive_path)) as archive:
            initialize_archive(archive)
            old = review(archive, before.payload)
        with closing(sqlite3.connect(source_path)) as source:
            edition = amend(source, recording_id, original_id, 10, 240)
            short = issue(source, recording_id, edition, ClosedWindow(20, 40))
        source_path.unlink()
        with closing(sqlite3.connect(archive_path)) as archive:
            initialize_archive(archive)
            signed = review(archive, short.payload)
        with closing(sqlite3.connect(archive_path)) as archive:
            prior = read(archive, old.report.report_id)
            current = read(archive, signed.report.report_id)

    details = [(row.beat, row.value, row.origin) for row in signed.report.content.details]
    saturated = signed.report.content.saturated
    expected_details = [(20, 255, 'rig-07/run-004/B'), (40, 252, 'rig-07/run-004/C')]
    if original.ledger.total != 767 or details != expected_details:
        raise RuntimeError('unexpected original ledger or offline detail')
    if signed.report.content.total != 507 or saturated != ((20, 'rig-07/run-004/B'),):
        raise RuntimeError('unexpected offline saturation ledger')
    if prior != old or prior.report.content.total != 757 or current != signed:
        raise RuntimeError('reopened archive differs from signed results')
    binding = current.report.content.packet_id == current.receipt.packet_id == short.packet_id
    if not binding or prior.receipt.packet_id != before.packet_id:
        raise RuntimeError('archive receipt lost its actual parent packet')
    print(json.dumps({
        'recording': recording_id,
        'original_total': original.ledger.total,
        'window': [20, 40],
        'report_id': current.report.report_id,
        'details': details,
        'total': current.report.content.total,
        'saturated': saturated,
        'receipt': current.receipt.receipt_id,
        'parent_packet_matches': binding,
        'reopened_previous_report': prior.report.report_id,
        'previous_total': prior.report.content.total,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()

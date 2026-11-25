import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from archive.commit import initialize_archive
from capture.intake import ingest
from capture.records import initialize_recordings
from publication.store import initialize_packets
from revision.history import initialize_editions
from sample_data import load_sample_recording


ORIGIN_A = 'rig-07/run-004/A'
ORIGIN_B = 'rig-07/run-004/B'
ORIGIN_C = 'rig-07/run-004/C'
ORIGIN_D = 'rig-07/run-004/D'
ORIGIN_E = 'rig-07/run-004/E'


class LabCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='test-strain-handoff-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source_path = self.root / 'source.sqlite'
        self.archive_path = self.root / 'archive.sqlite'

    @contextmanager
    def source(self):
        with closing(sqlite3.connect(self.source_path)) as connection:
            initialize_recordings(connection)
            initialize_editions(connection)
            initialize_packets(connection)
            yield connection

    @contextmanager
    def archive(self):
        with closing(sqlite3.connect(self.archive_path)) as connection:
            initialize_archive(connection)
            yield connection

    def ingest_sample(self, connection):
        recording_id, frames = load_sample_recording()
        return ingest(connection, recording_id, frames)


def independent_payload(*, packet_id, edition_id, window, frames, content):
    """Build an external V1 exchange item using the documented wire fields."""
    return json.dumps({
        'version': 1,
        'packet_id': packet_id,
        'recording_id': 'rig07-run004',
        'edition_id': edition_id,
        'window': list(window),
        'frames': [dict(zip(('beat', 'kind', 'raw', 'origin'), row, strict=True)) for row in frames],
        'content': content,
    }).encode('utf-8')


def detail_rows(archived):
    return [[row.beat, row.value, row.origin] for row in archived.report.content.details]


def archive_counts(connection):
    return {
        'reports': connection.execute('SELECT COUNT(*) FROM signed_reports').fetchone()[0],
        'details': connection.execute('SELECT COUNT(*) FROM report_details').fetchone()[0],
        'receipts': connection.execute('SELECT COUNT(*) FROM success_receipts').fetchone()[0],
    }


def emit_scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual,
                  code_paths, oracle):
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id,
        'input': inputs,
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': code_paths,
        'oracle': oracle,
    }, ensure_ascii=False, separators=(',', ':')))

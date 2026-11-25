"""Deterministic recording and transport fixtures for the two-station exercise."""
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.commit import initialize_archive
from archive.lookup import read
from capture.intake import ingest
from capture.records import initialize_recordings
from publication.issue import issue
from publication.store import initialize_packets, load_packet
from publication.window import ClosedWindow
from review.sign import review
from revision.amend import amend
from revision.history import initialize_editions, original_edition_id
from sample_data import load_sample_recording


PRIMARY = "bench-main"
STANDBY = "bench-spare"
WINDOW = ClosedWindow(20, 40)
DETAILS = ((20, 255, "rig-07/run-004/B"), (40, 252, "rig-07/run-004/C"))
SATURATED = ((20, "rig-07/run-004/B"),)


@contextmanager
def sample_source(path):
    with closing(sqlite3.connect(path)) as source:
        initialize_recordings(source)
        initialize_editions(source)
        initialize_packets(source)
        recording_id, frames = load_sample_recording()
        ingest(source, recording_id, frames)
        edition = amend(source, recording_id, original_edition_id(recording_id), 10, 240)
        yield source, recording_id, edition


@contextmanager
def archive_at(path):
    with closing(sqlite3.connect(path, cached_statements=0)) as db:
        initialize_archive(db)
        yield db


class Courier:
    def __init__(self):
        self.queue = []

    def put(self, messages):
        self.queue.extend(messages)

    def take(self, index=0):
        return self.queue.pop(index)

    def deliver(self, receive, index=0):
        return receive(self.take(index))


@contextmanager
def deny_commit(db):
    def authorize(action, name, _argument, _database, _trigger):
        if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    try:
        yield
    finally:
        db.set_authorizer(None)


class LostReply(Exception):
    """The operation returned, but its result was dropped by the caller."""


def lose_reply(operation, *args, **kwargs):
    operation(*args, **kwargs)
    raise LostReply("operation returned before the reply was dropped")


def check_sample(archived, packet_id):
    report = archived.report.content
    details = tuple((row.beat, row.value, row.origin) for row in report.details)
    if details != DETAILS or report.total != 507 or report.saturated != SATURATED:
        raise AssertionError("unexpected sample readings or saturation")
    if report.packet_id != packet_id or archived.receipt.packet_id != packet_id:
        raise AssertionError("report or receipt references another packet")
    if archived.receipt.report_id != archived.report.report_id:
        raise AssertionError("receipt references another report")


def main():
    """Run the ordinary fixed-packet path on separate archive files."""
    with TemporaryDirectory(prefix="strain-stations-") as directory:
        root = Path(directory)
        source_path = root / "source.sqlite"
        with sample_source(source_path) as (source, recording_id, edition):
            packet = issue(source, recording_id, edition, WINDOW)
            if load_packet(source, packet.packet_id) != packet:
                raise AssertionError("issued bytes could not be read back")
        source_path.unlink()
        for station in (PRIMARY, STANDBY):
            archive_path = root / f"{station}.sqlite"
            with archive_at(archive_path) as db:
                first = review(db, packet.payload)
                check_sample(first, packet.packet_id)
            with archive_at(archive_path) as reopened:
                saved = read(reopened, first.report.report_id)
                if saved != first:
                    raise AssertionError("archive did not preserve the original report")
                print(json.dumps({
                    "station": station,
                    "packet_id": saved.report.content.packet_id,
                    "report_id": saved.report.report_id,
                    "total": saved.report.content.total,
                    "saturated": saved.report.content.saturated,
                    "details": [(row.beat, row.value, row.origin)
                                for row in saved.report.content.details],
                    "receipt_id": saved.receipt.receipt_id,
                }, sort_keys=True))


if __name__ == "__main__":
    main()

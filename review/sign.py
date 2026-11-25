import sqlite3

from archive.commit import commit_signed_report
from archive.reconcile import ArchivedReport
from archive.reports import ReportDetail, ReportDraft
from review.intake import accept_packet
from review.interpret import interpret_packet
from review.tally import build_summary


def review(connection: sqlite3.Connection, payload: bytes) -> ArchivedReport:
    accepted = accept_packet(payload)
    packet = accepted.packet
    readings = interpret_packet(accepted)
    summary = build_summary(readings)
    draft = ReportDraft(
        packet_id=packet.packet_id,
        recording_id=packet.recording_id,
        edition_id=packet.edition_id,
        window_start=packet.window.start,
        window_end=packet.window.end,
        details=tuple(ReportDetail(reading.beat, reading.value, reading.origin) for reading in readings),
        total=summary.total,
        saturated=summary.saturated,
    )
    return commit_signed_report(connection, draft)

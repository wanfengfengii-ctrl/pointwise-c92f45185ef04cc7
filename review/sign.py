import sqlite3

from archive.commit import commit_handoff_report, commit_signed_report
from archive.handoff import load_handoff_item, local_role
from archive.reconcile import ArchivedReport
from archive.reports import ReportDetail, ReportDraft
from review.intake import accept_handoff_packet, accept_packet
from review.interpret import interpret_packet
from review.tally import build_summary


def review(connection: sqlite3.Connection, payload: bytes) -> ArchivedReport:
    accepted = accept_packet(payload)
    draft = _build_draft(accepted.packet, accepted)
    return commit_signed_report(connection, draft)


def review_handoff(
    connection: sqlite3.Connection, payload: bytes
) -> tuple[str, str | None, ArchivedReport | None]:
    """Sign a V2 handoff packet when this bound station holds the single claim."""
    accepted = accept_handoff_packet(payload)
    packet = accepted.packet
    role, _peer = local_role(connection, packet)
    item = load_handoff_item(connection, packet.packet_id)
    if item is None:
        eligible = role == 'primary'
    elif item.status == 'pending':
        return 'pending', None, None
    elif item.status == 'withdrawn':
        eligible = role == 'primary'
    else:  # transferred
        eligible = role == 'standby'
    if not eligible:
        return 'ineligible', None, None
    draft = _build_draft(packet, accepted)
    archived = commit_handoff_report(connection, draft)
    return 'signed', _bound_name(connection), archived


def _bound_name(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        'SELECT station_id FROM handoff_station WHERE rowid = 1'
    ).fetchone()
    return row[0]


def _build_draft(packet, accepted) -> ReportDraft:
    readings = interpret_packet(accepted)
    summary = build_summary(readings)
    return ReportDraft(
        packet_id=packet.packet_id,
        recording_id=packet.recording_id,
        edition_id=packet.edition_id,
        window_start=packet.window.start,
        window_end=packet.window.end,
        details=tuple(ReportDetail(reading.beat, reading.value, reading.origin) for reading in readings),
        total=summary.total,
        saturated=summary.saturated,
    )

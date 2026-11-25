import sqlite3

from archive.handoff import initialize_handoff
from archive.receipts import initialize_receipts, load_receipt, write_receipt
from archive.reconcile import ArchivedReport, reconcile_report
from archive.reports import ReportDraft, initialize_reports, load_report, write_report


def initialize_archive(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError('archive initialization requires an idle connection')
    connection.execute('PRAGMA foreign_keys = ON')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        initialize_reports(connection)
        initialize_receipts(connection)
        initialize_handoff(connection)


def commit_signed_report(connection: sqlite3.Connection, draft: ReportDraft) -> ArchivedReport:
    if connection.in_transaction:
        raise RuntimeError('report signing requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        report = write_report(connection, draft)
        receipt = write_receipt(connection, report.report_id, draft.packet_id)
        archived = reconcile_report(report, receipt)
    return archived


def commit_handoff_report(connection: sqlite3.Connection, draft: ReportDraft) -> ArchivedReport:
    """Sign a V2 packet: eligibility check, report, receipt and claim atomically."""
    if connection.in_transaction:
        raise RuntimeError('report signing requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute(
            'SELECT report_id FROM handoff_reports WHERE packet_id = ?', (draft.packet_id,)
        ).fetchone()
        if row is not None:
            report = load_report(connection, row[0])
            if report is None:
                raise ValueError(f'handoff claim for {draft.packet_id} lost its report')
            return reconcile_report(report, load_receipt(connection, report.report_id))
        report = write_report(connection, draft)
        receipt = write_receipt(connection, report.report_id, draft.packet_id)
        connection.execute(
            'INSERT INTO handoff_reports (packet_id, report_id) VALUES (?, ?)',
            (draft.packet_id, report.report_id),
        )
        archived = reconcile_report(report, receipt)
    return archived

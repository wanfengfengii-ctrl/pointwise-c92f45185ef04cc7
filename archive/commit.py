import sqlite3

from archive.receipts import initialize_receipts, write_receipt
from archive.reconcile import ArchivedReport, reconcile_report
from archive.reports import ReportDraft, initialize_reports, write_report


def initialize_archive(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError('archive initialization requires an idle connection')
    connection.execute('PRAGMA foreign_keys = ON')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        initialize_reports(connection)
        initialize_receipts(connection)


def commit_signed_report(connection: sqlite3.Connection, draft: ReportDraft) -> ArchivedReport:
    if connection.in_transaction:
        raise RuntimeError('report signing requires an idle connection')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        report = write_report(connection, draft)
        receipt = write_receipt(connection, report.report_id, draft.packet_id)
        archived = reconcile_report(report, receipt)
    return archived

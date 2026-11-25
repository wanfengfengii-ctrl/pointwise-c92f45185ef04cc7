import sqlite3

from archive.receipts import load_receipt
from archive.reconcile import ArchivedReport, reconcile_report
from archive.reports import load_report


def read(connection: sqlite3.Connection, report_id: int) -> ArchivedReport | None:
    if type(report_id) is not int or not 1 <= report_id <= (1 << 63) - 1:
        return None
    report = load_report(connection, report_id)
    if report is None:
        return None
    return reconcile_report(report, load_receipt(connection, report_id))


def read_handoff(
    connection: sqlite3.Connection, report_id: int
) -> tuple[str, ArchivedReport] | None:
    """Return the actual signing station and report for a V2 handoff report ID."""
    if type(report_id) is not int or not 1 <= report_id <= (1 << 63) - 1:
        return None
    row = connection.execute(
        'SELECT station_id FROM handoff_station WHERE rowid = 1'
    ).fetchone()
    claim = connection.execute(
        'SELECT 1 FROM handoff_reports WHERE report_id = ?', (report_id,)
    ).fetchone()
    if row is None or claim is None:
        return None
    report = load_report(connection, report_id)
    if report is None:
        return None
    return row[0], reconcile_report(report, load_receipt(connection, report_id))

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

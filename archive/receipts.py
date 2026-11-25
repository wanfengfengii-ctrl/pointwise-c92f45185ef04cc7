import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SuccessReceipt:
    receipt_id: str
    report_id: int
    packet_id: str


def initialize_receipts(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS success_receipts (
            report_id INTEGER PRIMARY KEY REFERENCES signed_reports(report_id),
            receipt_id TEXT NOT NULL UNIQUE,
            packet_id TEXT NOT NULL
        )
    ''')


def write_receipt(connection: sqlite3.Connection, report_id: int, packet_id: str) -> SuccessReceipt:
    if not connection.in_transaction:
        raise RuntimeError('receipt write requires a signing transaction')
    receipt = SuccessReceipt(f'receipt:{report_id}', report_id, packet_id)
    connection.execute('''
        INSERT INTO success_receipts (receipt_id, report_id, packet_id) VALUES (?, ?, ?)
    ''', (receipt.receipt_id, receipt.report_id, receipt.packet_id))
    return receipt


def load_receipt(connection: sqlite3.Connection, report_id: int) -> SuccessReceipt | None:
    row = connection.execute('''
        SELECT receipt_id, report_id, packet_id FROM success_receipts WHERE report_id = ?
    ''', (report_id,)).fetchone()
    return None if row is None else SuccessReceipt(*row)

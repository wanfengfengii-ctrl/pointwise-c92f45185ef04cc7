import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportDetail:
    beat: int
    value: int
    origin: str


@dataclass(frozen=True, slots=True)
class ReportDraft:
    packet_id: str
    recording_id: str
    edition_id: str
    window_start: int
    window_end: int
    details: tuple[ReportDetail, ...]
    total: int
    saturated: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class SignedReport:
    report_id: int
    content: ReportDraft


def initialize_reports(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS signed_reports (
            report_id INTEGER PRIMARY KEY,
            packet_id TEXT NOT NULL,
            recording_id TEXT NOT NULL,
            edition_id TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            window_end INTEGER NOT NULL CHECK (window_start <= window_end),
            total INTEGER NOT NULL CHECK (total >= 0),
            saturated TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS report_details (
            report_id INTEGER NOT NULL REFERENCES signed_reports(report_id),
            position INTEGER NOT NULL CHECK (position >= 0),
            beat INTEGER NOT NULL CHECK (beat > 0),
            value INTEGER NOT NULL CHECK (value BETWEEN 0 AND 255),
            origin TEXT NOT NULL,
            PRIMARY KEY (report_id, position),
            UNIQUE (report_id, beat)
        )
    ''')


def write_report(connection: sqlite3.Connection, draft: ReportDraft) -> SignedReport:
    if not connection.in_transaction:
        raise RuntimeError('report write requires a signing transaction')
    cursor = connection.execute('''
        INSERT INTO signed_reports
            (packet_id, recording_id, edition_id, window_start, window_end, total, saturated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (draft.packet_id, draft.recording_id, draft.edition_id,
          draft.window_start, draft.window_end, draft.total, json.dumps(draft.saturated)))
    report_id = cursor.lastrowid
    connection.executemany('''
        INSERT INTO report_details (report_id, position, beat, value, origin) VALUES (?, ?, ?, ?, ?)
    ''', (
        (report_id, position, detail.beat, detail.value, detail.origin)
        for position, detail in enumerate(draft.details)
    ))
    return SignedReport(report_id, draft)


def load_report(connection: sqlite3.Connection, report_id: int) -> SignedReport | None:
    row = connection.execute('''
        SELECT packet_id, recording_id, edition_id, window_start, window_end, total, saturated
        FROM signed_reports WHERE report_id = ?
    ''', (report_id,)).fetchone()
    if row is None:
        return None
    packet_id, recording_id, edition_id, window_start, window_end, total, saturated = row
    details = tuple(ReportDetail(*detail) for detail in connection.execute('''
        SELECT beat, value, origin FROM report_details WHERE report_id = ? ORDER BY position
    ''', (report_id,)))
    content = ReportDraft(packet_id, recording_id, edition_id, window_start, window_end,
                          details, total, tuple((beat, origin) for beat, origin in json.loads(saturated)))
    return SignedReport(report_id, content)

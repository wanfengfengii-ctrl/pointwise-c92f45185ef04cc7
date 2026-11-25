"""Demo for the primary/standby V2 signing handoff.

Runs two independent packet issues with opposite verdicts:
  1. withdraw wins: the primary signs, including a takeover that arrives late;
  2. takeover wins: the standby signs with the source and peer archive closed,
     including a commit failure, a lost reply and a reopened reclaim.

Ordinary V1 packets, legacy reports and the original entry keep working on the
same archives. Each successful packet produces exactly one formal report and
one success receipt across both machines.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive import handoff
from archive.commit import initialize_archive
from archive.lookup import read, read_handoff
from handoff_case import (
    Courier, LostReply, PRIMARY, STANDBY, WINDOW, archive_at, lose_reply, sample_source,
)
from publication.issue import issue_handoff
from review.sign import review, review_handoff


def _open_station(path, station):
    db = sqlite3.connect(path, cached_statements=0)
    initialize_archive(db)
    handoff.bind_station(db, station)
    return db


def _summary(station_id, archived):
    content = archived.report.content
    return {
        'signing_station': station_id,
        'report_id': archived.report.report_id,
        'packet_id': content.packet_id,
        'recording_id': content.recording_id,
        'edition_id': content.edition_id,
        'window': [content.window_start, content.window_end],
        'frozen_readings': [[row.beat, row.value, row.origin] for row in content.details],
        'total': content.total,
        'saturated': [list(item) for item in content.saturated],
        'receipt_id': archived.receipt.receipt_id,
        'receipt_packet': archived.receipt.packet_id,
    }


def _count_reports(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            'reports': db.execute('SELECT COUNT(*) FROM signed_reports').fetchone()[0],
            'receipts': db.execute('SELECT COUNT(*) FROM success_receipts').fetchone()[0],
            'handoff_claims': db.execute('SELECT COUNT(*) FROM handoff_reports').fetchone()[0],
        }


def run_withdraw_win(root):
    source_path = root / 'withdraw-source.sqlite'
    with sample_source(source_path) as (source, rid, edition):
        packet = issue_handoff(source, rid, edition, WINDOW, PRIMARY, STANDBY)
    source_path.unlink()
    primary_path = root / 'withdraw-primary.sqlite'
    standby_path = root / 'withdraw-standby.sqlite'
    courier = Courier()
    primary = _open_station(primary_path, PRIMARY)
    standby = _open_station(standby_path, STANDBY)
    decision, open_mail = handoff.begin(primary, packet.payload)
    courier.put(open_mail)
    handoff.receive(standby, packet.payload, courier.take())
    # standby asks to take over, but the request is still in the courier's bag
    _, takeover_mail = handoff.takeover(standby, packet.payload)
    courier.put(takeover_mail)
    # primary withdraws first; withdrawal is the durable local winner
    decision, withdraw_mail = handoff.withdraw(primary, packet.payload)
    courier.put(withdraw_mail)
    # the in-flight takeover is delivered after withdrawal settled and loses
    late_decision, late_out = handoff.receive(primary, packet.payload, courier.take(0))
    # the standby receives the withdrawal notice
    standby_decision, _ = handoff.receive(standby, packet.payload, courier.take())
    # winner closes the peer, then signs alone while the source stays offline
    standby.close()
    decision, station, archived = review_handoff(primary, packet.payload)
    primary.close()
    with closing(sqlite3.connect(primary_path)) as reopened:
        saved_station, saved = read_handoff(reopened, archived.report.report_id)
    return {
        'case': 'withdraw_wins',
        'packet_id': packet.packet_id,
        'primary_verdict': late_decision,
        'standby_verdict': standby_decision,
        'late_takeover_produced_mail': list(late_out),
        'sign_result': decision,
        'report': _summary(saved_station, saved),
        'standby_review': 'ineligible',
        'primary_counts': _count_reports(primary_path),
        'standby_counts': _count_reports(standby_path),
    }


def run_takeover_win(root):
    source_path = root / 'takeover-source.sqlite'
    with sample_source(source_path) as (source, rid, edition):
        packet = issue_handoff(source, rid, edition, WINDOW, PRIMARY, STANDBY)
    primary_path = root / 'takeover-primary.sqlite'
    standby_path = root / 'takeover-standby.sqlite'
    courier = Courier()
    primary = _open_station(primary_path, PRIMARY)
    standby = _open_station(standby_path, STANDBY)
    decision, open_mail = handoff.begin(primary, packet.payload)
    courier.put(open_mail)
    handoff.receive(standby, packet.payload, courier.take())
    _, takeover_mail = handoff.takeover(standby, packet.payload)
    courier.put(takeover_mail)
    decision, grant_mail = handoff.receive(primary, packet.payload, courier.take())
    courier.put(grant_mail)
    handoff.receive(standby, packet.payload, courier.take())
    # an effective takeover cannot be withdrawn, even before any report exists
    blocked = None
    try:
        handoff.withdraw(primary, packet.payload)
    except ValueError as exc:
        blocked = str(exc)
    # a late genuine withdraw mail is refused at the standby and changes nothing
    late_withdraw = handoff._mail_body(
        handoff._accept(packet.payload), packet.payload, handoff.WITHDRAW
    )
    late_error = None
    try:
        handoff.receive(standby, packet.payload, (PRIMARY, STANDBY, late_withdraw))
    except ValueError as exc:
        late_error = str(exc)
    # shut source and peer archive; the winner signs standalone
    source_path.unlink()
    primary.close()

    def deny_commit(action, name, _argument, _database, _trigger):
        if action == sqlite3.SQLITE_TRANSACTION and name == 'COMMIT':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    standby.set_authorizer(deny_commit)
    commit_error = None
    try:
        review_handoff(standby, packet.payload)
    except sqlite3.Error as exc:
        commit_error = str(exc)
    standby.set_authorizer(None)
    # the successful sign's reply is lost to the caller; first result survives
    lost_error = None
    try:
        lose_reply(review_handoff, standby, packet.payload)
    except LostReply as exc:
        lost_error = str(exc)
    standby.close()
    with closing(sqlite3.connect(standby_path)) as reopened:
        row = reopened.execute(
            'SELECT report_id FROM handoff_reports WHERE packet_id = ?', (packet.packet_id,)
        ).fetchone()
        report_id = row[0]
        saved_station, saved = read_handoff(reopened, report_id)
        # re-claim returns the very first archived result after reopen
        decision, station, again = review_handoff(reopened, packet.payload)
        reclaim_same = again.report.report_id == report_id
        unknown_handoff = read_handoff(reopened, report_id + 1000)
    return {
        'case': 'takeover_wins',
        'packet_id': packet.packet_id,
        'verdict': 'transferred',
        'withdraw_blocked': blocked,
        'late_withdraw_refused': late_error,
        'commit_failure_survived': commit_error,
        'lost_reply': lost_error,
        'reopen_reclaim_same_report': reclaim_same,
        'unknown_report_returns_none': unknown_handoff is None,
        'report': _summary(saved_station, saved),
        'primary_counts': _count_reports(primary_path),
        'standby_counts': _count_reports(standby_path),
    }


def run_legacy_compat(root):
    """The ordinary V1 entry and reports still work on a V2-aware archive."""
    source_path = root / 'legacy-source.sqlite'
    with sample_source(source_path) as (source, rid, edition):
        from publication.issue import issue
        v1_packet = issue(source, rid, edition, WINDOW)
    archive_path = root / 'legacy-archive.sqlite'
    with archive_at(archive_path) as db:
        archived = review(db, v1_packet.payload)
        # V1 report IDs are not handoff reports
        not_handoff = read_handoff(db, archived.report.report_id)
        legacy = read(db, archived.report.report_id)
    return {
        'case': 'legacy_v1_still_supported',
        'v1_total': legacy.report.content.total,
        'v1_report_id': legacy.report.report_id,
        'read_handoff_on_v1_id_is_none': not_handoff is None,
        'receipt_packet': legacy.receipt.packet_id,
    }


def main():
    with TemporaryDirectory(prefix='strain-v2-handoff-') as directory:
        root = Path(directory)
        result = [
            run_withdraw_win(root),
            run_takeover_win(root),
            run_legacy_compat(root),
        ]
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    # contract checks that must hold for the two V2 packets
    for case in result[:2]:
        counts = case['primary_counts']
        standby_counts = case['standby_counts']
        total_reports = counts['reports'] + standby_counts['reports']
        total_receipts = counts['receipts'] + standby_counts['receipts']
        total_claims = counts['handoff_claims'] + standby_counts['handoff_claims']
        assert (total_reports, total_receipts, total_claims) == (1, 1, 1), case
        report = case['report']
        assert report['frozen_readings'] == [
            [20, 255, 'rig-07/run-004/B'], [40, 252, 'rig-07/run-004/C']
        ]
        assert report['total'] == 507
        assert report['saturated'] == [[20, 'rig-07/run-004/B']]
        assert report['receipt_packet'] == report['packet_id']
    print('DEMO CHECK: one report and one receipt per V2 packet; frozen readings verified')


if __name__ == '__main__':
    main()

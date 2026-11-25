import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from archive import handoff
from archive.commit import initialize_archive
from archive.lookup import read, read_handoff
from handoff_case import (
    Courier, DETAILS, LostReply, PRIMARY, SATURATED, STANDBY, WINDOW, archive_at,
    lose_reply, sample_source,
)
from publication.issue import issue, issue_handoff
from publication.store import load_packet
from review.sign import review, review_handoff


class HandoffProtocolCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='test-handoff-protocol-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.primary_path = self.root / f'{PRIMARY}.sqlite'
        self.standby_path = self.root / f'{STANDBY}.sqlite'
        self._source_counter = 0
        self._connections = []
        self.source_paths = []

    def issue_v2(self):
        self._source_counter += 1
        source_path = self.root / f'source-{self._source_counter}.sqlite'
        self.source_paths.append(source_path)
        with sample_source(source_path) as (source, recording_id, edition):
            packet = issue_handoff(
                source, recording_id, edition, WINDOW, PRIMARY, STANDBY
            )
            self.assertEqual(load_packet(source, packet.packet_id).payload, packet.payload)
        envelope = json.loads(packet.payload.decode('utf-8'))
        self.assertEqual(envelope['version'], 2)
        self.assertEqual(
            set(envelope),
            {'version', 'packet_id', 'recording_id', 'edition_id', 'window', 'frames',
             'content', 'primary_id', 'standby_id'},
        )
        self.assertEqual((envelope['primary_id'], envelope['standby_id']), (PRIMARY, STANDBY))
        return packet

    def issue_v1_packet(self):
        self._source_counter += 1
        source_path = self.root / f'v1-source-{self._source_counter}.sqlite'
        self.source_paths.append(source_path)
        with sample_source(source_path) as (source, recording_id, edition):
            return issue(source, recording_id, edition, WINDOW)

    def open_archives(self):
        primary = sqlite3.connect(self.primary_path, cached_statements=0)
        standby = sqlite3.connect(self.standby_path, cached_statements=0)
        self._connections.extend((primary, standby))
        initialize_archive(primary)
        initialize_archive(standby)
        handoff.bind_station(primary, PRIMARY)
        handoff.bind_station(standby, STANDBY)
        return primary, standby

    def reopen(self, path):
        db = sqlite3.connect(path, cached_statements=0)
        self._connections.append(db)
        initialize_archive(db)
        return db

    def tearDown(self):
        for db in self._connections:
            try:
                db.close()
            except sqlite3.Error:
                pass
        super().tearDown()

    def deliver_to(self, db, payload, mail):
        decision, outgoing = handoff.receive(db, payload, mail)
        return decision, outgoing

    def assert_single_report_pair(self, packet_id):
        with self.reopen(self.primary_path) as primary, self.reopen(self.standby_path) as standby:
            counts = []
            for db in (primary, standby):
                counts.append({
                    'reports': db.execute('SELECT COUNT(*) FROM signed_reports').fetchone()[0],
                    'receipts': db.execute('SELECT COUNT(*) FROM success_receipts').fetchone()[0],
                    'claims': db.execute('SELECT COUNT(*) FROM handoff_reports').fetchone()[0],
                })
            total_reports = sum(item['reports'] for item in counts)
            total_receipts = sum(item['receipts'] for item in counts)
            total_claims = sum(item['claims'] for item in counts)
            self.assertEqual((total_reports, total_receipts, total_claims), (1, 1, 1))


class InitialEligibilityTests(HandoffProtocolCase):
    def test_initial_primary_may_sign_standby_has_no_claim(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        self.assertEqual(handoff.state(primary, packet.payload)[0], 'initial')
        self.assertEqual(handoff.state(standby, packet.payload)[0], 'initial')
        decision, station, archived = review_handoff(standby, packet.payload)
        self.assertEqual((decision, station, archived), ('ineligible', None, None))
        decision, station, archived = review_handoff(primary, packet.payload)
        self.assertEqual(decision, 'signed')
        self.assertEqual(station, PRIMARY)
        self._check_readings(archived, packet.packet_id)
        # the formally signed primary can no longer begin a handoff
        with self.assertRaisesRegex(ValueError, 'already formally signed'):
            handoff.begin(primary, packet.payload)
        # one report and one receipt overall
        self.assert_single_report_pair(packet.packet_id)

    def test_wrong_version_entries_raise_value_error(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        # build a V1 packet with its own id through the source
        v1 = self.issue_v1_packet()
        with self.assertRaises(ValueError):
            handoff.begin(primary, v1.payload)
        with self.assertRaises(ValueError):
            review_handoff(primary, v1.payload)
        # V1 entry must reject V2
        with self.assertRaises(ValueError):
            review(primary, packet.payload)

    def test_station_binding_rules(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        # rebinding with the same name after reopen is allowed
        with self.reopen(self.primary_path) as reopened:
            handoff.bind_station(reopened, PRIMARY)
        with self.reopen(self.primary_path) as reopened:
            with self.assertRaisesRegex(ValueError, 'already bound'):
                handoff.bind_station(reopened, STANDBY)
        # a stranger station cannot handle the packet
        stranger_path = self.root / 'stranger.sqlite'
        with closing(sqlite3.connect(stranger_path)) as stranger:
            initialize_archive(stranger)
            handoff.bind_station(stranger, 'bench-other')
            for entry in (handoff.begin, handoff.withdraw, handoff.takeover):
                with self.assertRaisesRegex(ValueError, 'not a party'):
                    entry(stranger, packet.payload)
            with self.assertRaisesRegex(ValueError, 'not a party'):
                review_handoff(stranger, packet.payload)

    def _check_readings(self, archived, packet_id):
        content = archived.report.content
        details = tuple((row.beat, row.value, row.origin) for row in content.details)
        self.assertEqual(details, DETAILS)
        self.assertEqual(content.total, 507)
        self.assertEqual(content.saturated, SATURATED)
        self.assertEqual(content.packet_id, packet_id)
        self.assertEqual(archived.receipt.packet_id, packet_id)


class WithdrawWinTests(HandoffProtocolCase):
    def test_withdraw_wins_with_late_takeover(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        decision, open_mail = handoff.begin(primary, packet.payload)
        self.assertEqual(decision, 'pending')
        courier.put(open_mail)
        # standby opens and asks to take over, but the request stays in transit
        _, _ = self.deliver_to(standby, packet.payload, courier.take())
        decision, takeover_mail = handoff.takeover(standby, packet.payload)
        self.assertEqual(decision, 'pending')
        courier.put(takeover_mail)
        # primary withdraws first and wins
        decision, withdraw_mail = handoff.withdraw(primary, packet.payload)
        self.assertEqual(decision, 'withdrawn')
        courier.put(withdraw_mail)
        # the in-flight takeover arrives too late: it must not change the verdict
        late_takeover = courier.take(0)
        decision, outgoing = self.deliver_to(primary, packet.payload, late_takeover)
        self.assertEqual(decision, 'withdrawn')
        self.assertEqual(outgoing, ())
        # duplicate late takeover is also harmless
        decision, _ = self.deliver_to(primary, packet.payload, late_takeover)
        self.assertEqual(decision, 'withdrawn')
        # standby learns of withdrawal
        decision, _ = self.deliver_to(standby, packet.payload, courier.take())
        self.assertEqual(decision, 'withdrawn')
        # standby cannot take over after withdrawal settled
        with self.assertRaisesRegex(ValueError, 'already won withdrawal'):
            handoff.takeover(standby, packet.payload)
        # primary signs alone, peer archive stays untouched
        decision, station, archived = review_handoff(primary, packet.payload)
        self.assertEqual((decision, station), ('signed', PRIMARY))
        self.assertEqual(tuple((r.beat, r.value, r.origin) for r in archived.report.content.details),
                         DETAILS)
        self.assertEqual(review_handoff(standby, packet.payload)[0], 'ineligible')
        self.assert_single_report_pair(packet.packet_id)

    def test_withdraw_idempotent_and_reopen_resumes(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        _, open_mail = handoff.begin(primary, packet.payload)
        courier.put(open_mail)
        self.deliver_to(standby, packet.payload, courier.take())
        # close both archives while pending, then reopen via state()
        primary.close()
        standby.close()
        with self.reopen(self.primary_path) as primary, self.reopen(self.standby_path) as standby:
            decision, pending_out = handoff.state(primary, packet.payload)
            self.assertEqual(decision, 'pending')
            # retrying begin returns the same open mail again
            retry_decision, retry_out = handoff.begin(primary, packet.payload)
            self.assertEqual(retry_decision, 'pending')
            self.assertEqual(retry_out, pending_out)
            decision, withdraw_mail = handoff.withdraw(primary, packet.payload)
            self.assertEqual(decision, 'withdrawn')
            self.assertEqual(json.loads(withdraw_mail[0][2])['kind'], 'withdraw')
            # withdraw is idempotent: same verdict, same withdraw mail tuple
            again_decision, again_out = handoff.withdraw(primary, packet.payload)
            self.assertEqual(again_decision, 'withdrawn')
            self.assertEqual(again_out, withdraw_mail)
            # state() still lists every mail produced locally for redelivery
            decision, all_out = handoff.state(primary, packet.payload)
            self.assertEqual(decision, 'withdrawn')
            self.assertEqual(
                [json.loads(m[2])['kind'] for m in all_out], ['open', 'withdraw']
            )
            self.deliver_to(standby, packet.payload, withdraw_mail[0])
            self.assertEqual(handoff.state(standby, packet.payload)[0], 'withdrawn')


class TakeoverWinTests(HandoffProtocolCase):
    def _run_transfer(self, *, reopen_midway=False):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        _, open_mail = handoff.begin(primary, packet.payload)
        courier.put(open_mail)
        self.deliver_to(standby, packet.payload, courier.take())
        _, takeover_mail = handoff.takeover(standby, packet.payload)
        courier.put(takeover_mail)
        decision, grant_out = self.deliver_to(primary, packet.payload, courier.take())
        self.assertEqual(decision, 'transferred')
        self.assertEqual(len(grant_out), 1)
        grant = grant_out[0]
        courier.put(grant)
        if reopen_midway:
            primary.close()
            standby.close()
            primary = self.reopen(self.primary_path)
            standby = self.reopen(self.standby_path)
            # reopen still shows the settled / pending local verdicts and queued mails
            self.assertEqual(handoff.state(primary, packet.payload)[0], 'transferred')
            decision, resume_out = handoff.state(standby, packet.payload)
            self.assertEqual(decision, 'pending')
            # the standby still holds its takeover request for redelivery
            self.assertEqual(
                [json.loads(m[2])['kind'] for m in resume_out], ['takeover']
            )
            # the grant can be reclaimed from the primary outbox after reopen
            grant_mail = [m for m in handoff.state(primary, packet.payload)[1]
                          if json.loads(m[2])['kind'] == 'grant']
            self.assertEqual(len(grant_mail), 1)
            grant = grant_mail[0]
        self.deliver_to(standby, packet.payload, grant)
        self.assertEqual(handoff.state(primary, packet.payload)[0], 'transferred')
        self.assertEqual(handoff.state(standby, packet.payload)[0], 'transferred')
        return packet, primary, standby

    def test_takeover_wins_and_blocks_late_withdraw(self):
        packet, primary, standby = self._run_transfer()
        # withdrawal after an effective takeover is rejected even before signing
        with self.assertRaisesRegex(ValueError, 'effective takeover'):
            handoff.withdraw(primary, packet.payload)
        decision, station, archived = review_handoff(standby, packet.payload)
        self.assertEqual((decision, station), ('signed', STANDBY))
        self.assertEqual(review_handoff(primary, packet.payload)[0], 'ineligible')
        self.assert_single_report_pair(packet.packet_id)
        # retrieve by the local report number with the actual signing machine
        report_id = archived.report.report_id
        with self.reopen(self.standby_path) as reopened:
            station_id, saved = read_handoff(reopened, report_id)
            self.assertEqual(station_id, STANDBY)
            self.assertEqual(saved, archived)
            self.assertIsNone(read_handoff(reopened, report_id + 999))
            self.assertIsNone(read(reopened, report_id + 999))

    def test_late_withdraw_mail_rejected_after_transfer(self):
        packet, primary, standby = self._run_transfer()
        # craft a genuine withdraw body from the primary side machinery
        body = handoff._mail_body(
            handoff._accept(packet.payload), packet.payload, handoff.WITHDRAW
        )
        mail = (PRIMARY, STANDBY, body)
        with self.assertRaisesRegex(ValueError, 'conflicts with the settled transferred'):
            handoff.receive(standby, packet.payload, mail)
        # state unchanged
        self.assertEqual(handoff.state(standby, packet.payload)[0], 'transferred')

    def test_reopen_midway_and_out_of_order_duplicates(self):
        packet, primary, standby = self._run_transfer(reopen_midway=True)
        # re-drive the whole exchange out of order on fresh reopened archives
        primary.close()
        standby.close()
        with self.reopen(self.primary_path) as primary, self.reopen(self.standby_path) as standby:
            # replay takeover to primary twice
            body = handoff._mail_body(
                handoff._accept(packet.payload), packet.payload, handoff.TAKEOVER
            )
            takeover = (STANDBY, PRIMARY, body)
            decision, grant = handoff.receive(primary, packet.payload, takeover)
            self.assertEqual(decision, 'transferred')
            decision, again = handoff.receive(primary, packet.payload, takeover)
            self.assertEqual(decision, 'transferred')
            self.assertEqual(again, ())
            # a duplicate request produces no new mail; reclaim the grant from outbox
            grant_mail = [m for m in handoff.state(primary, packet.payload)[1]
                          if json.loads(m[2])['kind'] == 'grant'][0]
            # standby receives grant twice
            decision, _ = handoff.receive(standby, packet.payload, grant_mail)
            self.assertEqual(decision, 'transferred')
            decision, _ = handoff.receive(standby, packet.payload, grant_mail)
            self.assertEqual(decision, 'transferred')
            # open can still arrive late and repeatedly without damage
            open_body = handoff._mail_body(
                handoff._accept(packet.payload), packet.payload, handoff.OPEN
            )
            decision, _ = handoff.receive(standby, packet.payload, (PRIMARY, STANDBY, open_body))
            self.assertEqual(decision, 'transferred')

    def test_reopen_while_pending_resumes_with_state_outbox(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        _, open_mail = handoff.begin(primary, packet.payload)
        courier.put(open_mail)
        self.deliver_to(standby, packet.payload, courier.take())
        _, takeover_mail = handoff.takeover(standby, packet.payload)
        self.assertEqual(len(takeover_mail), 1)
        # both archives close while the request is still undecided
        primary.close()
        standby.close()
        with self.reopen(self.primary_path) as primary, self.reopen(self.standby_path) as standby:
            self.assertEqual(handoff.state(primary, packet.payload)[0], 'pending')
            self.assertEqual(handoff.state(standby, packet.payload)[0], 'pending')
            recovered = [m for m in handoff.state(standby, packet.payload)[1]
                         if json.loads(m[2])['kind'] == 'takeover']
            self.assertEqual(len(recovered), 1)
            decision, grant = handoff.receive(primary, packet.payload, recovered[0])
            self.assertEqual(decision, 'transferred')
            decision, _ = handoff.receive(standby, packet.payload, grant[0])
            self.assertEqual(decision, 'transferred')
            self.assertEqual(handoff.state(primary, packet.payload)[0], 'transferred')
            self.assertEqual(handoff.state(standby, packet.payload)[0], 'transferred')

    def test_pending_machines_cannot_sign(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        _, open_mail = handoff.begin(primary, packet.payload)
        courier.put(open_mail)
        self.deliver_to(standby, packet.payload, courier.take())
        _, takeover_mail = handoff.takeover(standby, packet.payload)
        # primary is pending until it adjudicates; neither side may sign
        self.assertEqual(review_handoff(primary, packet.payload)[0], 'pending')
        self.assertEqual(review_handoff(standby, packet.payload)[0], 'pending')
        # a second packet whose takeover was requested before the open mail is
        # also pending on both stations
        other = self.issue_v2()
        other_standby_path = self.root / f'{STANDBY}-other.sqlite'
        with archive_at(other_standby_path) as other_standby:
            handoff.bind_station(other_standby, STANDBY)
            decision, _ = handoff.takeover(other_standby, other.payload)
            self.assertEqual(decision, 'pending')
            self.assertEqual(review_handoff(other_standby, other.payload)[0], 'pending')


class MailValidationTests(HandoffProtocolCase):
    def test_wrong_packet_wrong_station_and_illegal_mail_rejected(self):
        packet = self.issue_v2()
        other = self.issue_v2()
        primary, standby = self.open_archives()
        _, open_mail = handoff.begin(primary, packet.payload)
        # wrong packet bytes with a mail referencing the other packet
        with self.assertRaisesRegex(ValueError, 'does not reference this exact packet'):
            handoff.receive(standby, other.payload, open_mail[0])
        # tampered body keeps the envelope but changes the declared kind
        sender, recipient, body = open_mail[0]
        content = json.loads(body)
        content['kind'] = 'takeover'
        tampered = (sender, recipient, json.dumps(content, sort_keys=True,
                                                  separators=(',', ':')).encode())
        with self.assertRaisesRegex(ValueError, 'do not match its declared role'):
            handoff.receive(standby, packet.payload, tampered)
        # wrong recipient station
        with self.assertRaisesRegex(ValueError, 'addressed to another station'):
            handoff.receive(primary, packet.payload, open_mail[0])
        # malformed mail shapes
        for bad_mail in (
            (PRIMARY, STANDBY, b''),
            (PRIMARY, STANDBY, 'not-bytes'),
            (PRIMARY, STANDBY),
            (PRIMARY, STANDBY, b'{}', 'extra'),
        ):
            with self.assertRaises(ValueError):
                handoff.receive(standby, packet.payload, bad_mail)
        # role reversal: primary cannot act as standby and vice versa
        with self.assertRaisesRegex(ValueError, 'only the standby'):
            handoff.takeover(primary, packet.payload)
        with self.assertRaisesRegex(ValueError, 'only the primary'):
            handoff.begin(standby, packet.payload)
        with self.assertRaisesRegex(ValueError, 'only the primary'):
            handoff.withdraw(standby, packet.payload)
        # state remains initial for the untouched packet at standby
        self.assertEqual(handoff.state(standby, packet.payload)[0], 'initial')
        # rejected mail must not create any mailbox row or item row
        self.assertEqual(
            standby.execute('SELECT COUNT(*) FROM handoff_mailbox').fetchone()[0], 0
        )
        self.assertEqual(
            standby.execute('SELECT COUNT(*) FROM handoff_items').fetchone()[0], 0
        )
        # a mail whose digest references other bytes is also refused without writes
        content = json.loads(body)
        content['digest'] = '0' * 64
        wrong_digest = (sender, recipient,
                        json.dumps(content, sort_keys=True, separators=(',', ':')).encode())
        with self.assertRaisesRegex(ValueError, 'does not reference this exact packet'):
            handoff.receive(standby, packet.payload, wrong_digest)
        self.assertEqual(
            standby.execute('SELECT COUNT(*) FROM handoff_mailbox').fetchone()[0], 0
        )
        # valid delivery still works after the rejections
        decision, _ = handoff.receive(standby, packet.payload, open_mail[0])
        self.assertEqual(decision, 'pending')

    def test_illegal_operations_leave_state_untouched(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        # the primary cannot withdraw a handoff it never initiated
        with self.assertRaisesRegex(ValueError, 'has not been opened'):
            handoff.withdraw(primary, packet.payload)
        # the primary cannot receive a takeover before initiating anything
        body_takeover = handoff._mail_body(
            handoff._accept(packet.payload), packet.payload, handoff.TAKEOVER
        )
        with self.assertRaisesRegex(ValueError, 'has not initiated'):
            handoff.receive(primary, packet.payload, (STANDBY, PRIMARY, body_takeover))
        # a standby-addressed mail whose envelope contradicts its body endpoints
        body_grant = handoff._mail_body(
            handoff._accept(packet.payload), packet.payload, handoff.GRANT
        )
        reversed_mail = (STANDBY, STANDBY, body_grant)
        with self.assertRaisesRegex(ValueError, 'envelope does not match its body endpoints'):
            handoff.receive(standby, packet.payload, reversed_mail)
        self.assertEqual(handoff.state(standby, packet.payload)[0], 'initial')
        # but the standby may register its takeover request with only the packet
        decision, outgoing = handoff.takeover(standby, packet.payload)
        self.assertEqual(decision, 'pending')
        self.assertEqual(json.loads(outgoing[0][2])['kind'], 'takeover')
        self.assertEqual(handoff.state(standby, packet.payload)[0], 'pending')

    def test_standby_accepts_primary_mails_before_open(self):
        # out-of-order first delivery: withdraw or grant may arrive before open
        for kind, expected in (
            (handoff.WITHDRAW, 'withdrawn'),
            (handoff.GRANT, 'transferred'),
        ):
            packet = self.issue_v2()
            standby_path = self.root / f'standby-early-{kind}.sqlite'
            with archive_at(standby_path) as early:
                handoff.bind_station(early, STANDBY)
                body = handoff._mail_body(
                    handoff._accept(packet.payload), packet.payload, kind
                )
                decision, _ = handoff.receive(
                    early, packet.payload, (PRIMARY, STANDBY, body)
                )
                self.assertEqual(decision, expected)
                # the late open mail cannot overturn the settled verdict
                open_body = handoff._mail_body(
                    handoff._accept(packet.payload), packet.payload, handoff.OPEN
                )
                decision, _ = handoff.receive(
                    early, packet.payload, (PRIMARY, STANDBY, open_body)
                )
                self.assertEqual(decision, expected)


class FailureAndReclaimTests(HandoffProtocolCase):
    def test_commit_failure_then_success_and_lost_reply_reclaim(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        _, open_mail = handoff.begin(primary, packet.payload)
        courier.put(open_mail)
        self.deliver_to(standby, packet.payload, courier.take())
        _, takeover_mail = handoff.takeover(standby, packet.payload)
        courier.put(takeover_mail)
        decision, grant_out = self.deliver_to(primary, packet.payload, courier.take())
        self.assertEqual(decision, 'transferred')
        grant = grant_out[0]
        self.deliver_to(standby, packet.payload, grant)
        # signing attempt whose COMMIT is denied: eligibility and history survive
        def deny(action, name, _argument, _database, _trigger):
            if action == sqlite3.SQLITE_TRANSACTION and name == 'COMMIT':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        standby.set_authorizer(deny)
        try:
            with self.assertRaises(sqlite3.Error):
                review_handoff(standby, packet.payload)
        finally:
            standby.set_authorizer(None)
        # the failed commit left no report, details, receipt or claim behind
        with self.reopen(self.standby_path) as check:
            self.assertEqual(check.execute('SELECT COUNT(*) FROM signed_reports').fetchone()[0], 0)
            self.assertEqual(check.execute('SELECT COUNT(*) FROM report_details').fetchone()[0], 0)
            self.assertEqual(check.execute('SELECT COUNT(*) FROM success_receipts').fetchone()[0], 0)
            self.assertEqual(check.execute('SELECT COUNT(*) FROM handoff_reports').fetchone()[0], 0)
            # the verdict survived, so the winner can retry the sign
            self.assertEqual(
                check.execute("SELECT status FROM handoff_items WHERE packet_id = ?",
                              (packet.packet_id,)).fetchone()[0],
                'transferred',
            )
        self.assertEqual(review_handoff(standby, packet.payload)[0], 'signed')
        # a failed/replayed sign never doubles the claim
        self.assertEqual(review_handoff(standby, packet.payload)[2].report.report_id,
                         review_handoff(standby, packet.payload)[2].report.report_id)
        # the caller loses the reply of a successful sign: reopening reclaims first result
        with self.assertRaises(LostReply):
            lose_reply(review_handoff, standby, packet.payload)
        with self.reopen(self.standby_path) as reopened:
            rows = reopened.execute(
                'SELECT report_id FROM handoff_reports WHERE packet_id = ?',
                (packet.packet_id,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            report_id = rows[0][0]
            station_id, saved = read_handoff(reopened, report_id)
            self.assertEqual(station_id, STANDBY)
            self.assertEqual(saved.report.content.total, 507)
        # re-sign returns the very first archived result
        decision, station, again = review_handoff(standby, packet.payload)
        self.assertEqual(decision, 'signed')
        self.assertEqual(again.report.report_id, report_id)
        self.assert_single_report_pair(packet.packet_id)

    def test_winner_signs_with_source_and_peer_closed(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        courier = Courier()
        _, open_mail = handoff.begin(primary, packet.payload)
        courier.put(open_mail)
        self.deliver_to(standby, packet.payload, courier.take())
        _, takeover_mail = handoff.takeover(standby, packet.payload)
        courier.put(takeover_mail)
        decision, grant_out = self.deliver_to(primary, packet.payload, courier.take())
        self.assertEqual(decision, 'transferred')
        grant = grant_out[0]
        self.deliver_to(standby, packet.payload, grant)
        # shut the source and the peer archive, then sign standalone
        for source_path in self.source_paths:
            if source_path.exists():
                source_path.unlink()
        primary.close()
        self.assertFalse(any(path.exists() for path in self.source_paths))
        with self.reopen(self.standby_path) as lone:
            decision, station, archived = review_handoff(lone, packet.payload)
            self.assertEqual((decision, station), ('signed', STANDBY))
            station_id, saved = read_handoff(lone, archived.report.report_id)
            self.assertEqual(station_id, STANDBY)
            self.assertEqual(saved, archived)


class LegacyCompatibilityTests(HandoffProtocolCase):
    def test_v1_ordinary_flow_still_works_alongside_v2(self):
        packet = self.issue_v2()
        primary, standby = self.open_archives()
        # a V1 packet signed through the original entry on the same archive
        v1 = self.issue_v1_packet()
        archived_v1 = review(primary, v1.payload)
        self.assertEqual(archived_v1.report.content.total, 507)
        # the V1 report id is not a handoff report
        self.assertIsNone(read_handoff(primary, archived_v1.report.report_id))
        # V2 still works independently on its own packet
        _, open_mail = handoff.begin(primary, packet.payload)
        self.deliver_to(standby, packet.payload, open_mail[0])
        _, takeover_mail = handoff.takeover(standby, packet.payload)
        decision, grant = self.deliver_to(primary, packet.payload, takeover_mail[0])
        self.assertEqual(decision, 'transferred')
        self.deliver_to(standby, packet.payload, grant[0])
        decision, station, archived_v2 = review_handoff(standby, packet.payload)
        self.assertEqual(station, STANDBY)
        # two packets, two reports, but only one V2 claim
        with self.reopen(self.primary_path) as p, self.reopen(self.standby_path) as s:
            self.assertEqual(p.execute('SELECT COUNT(*) FROM signed_reports').fetchone()[0], 1)
            self.assertEqual(s.execute('SELECT COUNT(*) FROM signed_reports').fetchone()[0], 1)
            self.assertEqual(s.execute('SELECT COUNT(*) FROM handoff_reports').fetchone()[0], 1)
            self.assertEqual(p.execute('SELECT COUNT(*) FROM handoff_reports').fetchone()[0], 0)
            # legacy read() still returns the ordinary V1 report
            self.assertEqual(read(p, archived_v1.report.report_id), archived_v1)


if __name__ == '__main__':
    unittest.main()

from dataclasses import dataclass

from archive.receipts import SuccessReceipt
from archive.reports import SignedReport


@dataclass(frozen=True, slots=True)
class ArchivedReport:
    report: SignedReport
    receipt: SuccessReceipt


def reconcile_report(report: SignedReport, receipt: SuccessReceipt | None) -> ArchivedReport:
    if receipt is None:
        raise ValueError(f'report {report.report_id} has no success receipt')
    if receipt.report_id != report.report_id or receipt.packet_id != report.content.packet_id:
        raise ValueError(f'receipt binding does not match report {report.report_id}')
    return ArchivedReport(report, receipt)

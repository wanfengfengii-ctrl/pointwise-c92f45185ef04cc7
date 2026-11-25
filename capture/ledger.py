from dataclasses import dataclass
from typing import Iterable

from capture.decoder import OriginalReading


@dataclass(frozen=True, slots=True)
class OriginalLedger:
    readings: tuple[OriginalReading, ...]
    total: int
    saturated: tuple[OriginalReading, ...]


def build_original_ledger(readings: Iterable[OriginalReading]) -> OriginalLedger:
    readings = tuple(readings)
    return OriginalLedger(
        readings=readings,
        total=sum(reading.value for reading in readings),
        saturated=tuple(reading for reading in readings if reading.value == 255),
    )

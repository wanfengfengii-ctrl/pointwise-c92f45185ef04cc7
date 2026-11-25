from dataclasses import dataclass

from review.interpret import ReviewReading


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    total: int
    saturated: tuple[tuple[int, str], ...]


def build_summary(readings: tuple[ReviewReading, ...]) -> ReviewSummary:
    return ReviewSummary(
        total=sum(reading.value for reading in readings),
        saturated=tuple((reading.beat, reading.origin) for reading in readings if reading.value == 255),
    )

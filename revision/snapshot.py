import sqlite3
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from capture.frames import validate_label
from capture.records import recording_exists
from revision.history import load_edition_link, original_edition_id
from revision.lineage import resolve_lineage


@dataclass(frozen=True, slots=True)
class EditionSnapshot:
    recording_id: str
    edition_id: str
    amendments: Mapping[int, int]


def load_snapshot(
    connection: sqlite3.Connection, recording_id: str, edition_id: str
) -> EditionSnapshot:
    validate_label(edition_id, 'edition ID')
    if not recording_exists(connection, recording_id):
        raise ValueError(f'unknown recording: {recording_id}')
    lineage = resolve_lineage(
        recording_id,
        edition_id,
        original_edition_id(recording_id),
        lambda edition: load_edition_link(connection, edition),
    )
    effective = {amendment.beat: amendment.value for amendment in lineage}
    return EditionSnapshot(recording_id, edition_id, MappingProxyType(effective))

from dataclasses import dataclass
from typing import Callable

from revision.targets import Amendment


@dataclass(frozen=True, slots=True)
class EditionLink:
    edition_id: str
    recording_id: str
    parent_id: str
    amendment: Amendment


def resolve_lineage(
    recording_id: str,
    edition_id: str,
    root_id: str,
    load_link: Callable[[str], EditionLink | None],
) -> tuple[Amendment, ...]:
    amendments = []
    seen = set()
    current = edition_id
    while current != root_id:
        if current in seen:
            raise ValueError(f'cycle in edition lineage at {current}')
        seen.add(current)
        link = load_link(current)
        if link is None or link.recording_id != recording_id or link.edition_id != current:
            raise ValueError(f'edition {current} does not belong to recording {recording_id}')
        amendments.append(link.amendment)
        current = link.parent_id
    return tuple(reversed(amendments))

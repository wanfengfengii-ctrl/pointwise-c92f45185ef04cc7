from dataclasses import dataclass

from capture.frames import validate_frame_order
from publication.envelope import Packet, decode_packet


@dataclass(frozen=True, slots=True)
class AcceptedPacket:
    packet: Packet


def accept_packet(payload: bytes) -> AcceptedPacket:
    packet = decode_packet(payload)
    validate_frame_order(packet.frames)
    for frame in packet.frames:
        if not packet.window.contains(frame.beat):
            raise ValueError(f'frame at beat {frame.beat} lies outside the packet window')
    return AcceptedPacket(packet)

from dataclasses import dataclass

from capture.frames import validate_frame_order
from publication.envelope import HandoffPacket, Packet, decode_handoff_packet, decode_packet


@dataclass(frozen=True, slots=True)
class AcceptedPacket:
    packet: Packet


@dataclass(frozen=True, slots=True)
class AcceptedHandoffPacket:
    packet: HandoffPacket


def accept_packet(payload: bytes) -> AcceptedPacket:
    packet = decode_packet(payload)
    _validate_packet(packet)
    return AcceptedPacket(packet)


def accept_handoff_packet(payload: bytes) -> AcceptedHandoffPacket:
    packet = decode_handoff_packet(payload)
    _validate_packet(packet)
    return AcceptedHandoffPacket(packet)


def _validate_packet(packet: Packet) -> None:
    validate_frame_order(packet.frames)
    for frame in packet.frames:
        if not packet.window.contains(frame.beat):
            raise ValueError(f'frame at beat {frame.beat} lies outside the packet window')

import argparse
import socket
import sys

from utils import PacketHeader, compute_checksum


def send_ack(sock, address, seq_num):
    header = PacketHeader(type=3, seq_num=seq_num, length=0)
    header.checksum = compute_checksum(header)
    sock.sendto(bytes(header), address)


def receiver(receiver_ip, receiver_port, window_size):
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((receiver_ip, receiver_port))
        peer = None
        expected = 1
        buffered = {}
        while True:
            packet, address = sock.recvfrom(2048)
            if len(packet) < 16 or len(packet) > 1472:
                continue
            header = PacketHeader(packet[:16])
            if len(packet) != 16 + header.length:
                continue
            payload = packet[16:]
            checksum = header.checksum
            header.checksum = 0
            if compute_checksum(header / payload) != checksum:
                continue
            if peer is None:
                if header.type == 0 and header.seq_num == 0 and header.length == 0:
                    peer = address
                    send_ack(sock, address, 1)
                continue
            if address != peer:
                continue
            if header.type == 2:
                if header.seq_num >= expected + window_size:
                    continue
                if expected <= header.seq_num:
                    buffered.setdefault(header.seq_num, payload)
                    while expected in buffered:
                        sys.stdout.buffer.write(buffered.pop(expected))
                        expected += 1
                    sys.stdout.buffer.flush()
                send_ack(sock, address, expected)
            elif header.type == 1 and header.length == 0 and header.seq_num == expected:
                send_ack(sock, address, expected + 1)
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("receiver_ip", help="The IP address of the host that receiver is running on")
    parser.add_argument("receiver_port", type=int, help="The port number on which receiver is listening")
    parser.add_argument("window_size", type=int, help="Maximum number of outstanding packets")
    args = parser.parse_args()
    receiver(args.receiver_ip, args.receiver_port, args.window_size)


if __name__ == "__main__":
    main()

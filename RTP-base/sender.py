import argparse
import socket
import sys
import time

from utils import PacketHeader, compute_checksum


TIMEOUT = 0.5
CHUNK_SIZE = 1456


def make_packet(packet_type, seq_num, data=b""):
    header = PacketHeader(type=packet_type, seq_num=seq_num, length=len(data))
    header.checksum = compute_checksum(header / data)
    return bytes(header / data)


def read_ack(packet):
    if len(packet) != 16:
        return None
    header = PacketHeader(packet)
    checksum = header.checksum
    header.checksum = 0
    if header.type != 3 or header.length != 0 or compute_checksum(header) != checksum:
        return None
    return header.seq_num


def sender(receiver_ip, receiver_port, window_size):
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    address = (socket.gethostbyname(receiver_ip), receiver_port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
        start = make_packet(0, 0)
        while True:
            sock.send(start)
            sock.settimeout(TIMEOUT)
            try:
                if read_ack(sock.recv(2048)) == 1:
                    break
            except socket.timeout:
                pass

        data = sys.stdin.buffer.read()
        packets = [make_packet(2, i // CHUNK_SIZE + 1, data[i:i + CHUNK_SIZE])
                   for i in range(0, len(data), CHUNK_SIZE)]
        base = 1
        next_seq = 1
        deadline = time.monotonic() + TIMEOUT
        while base <= len(packets):
            while next_seq <= len(packets) and next_seq < base + window_size:
                sock.send(packets[next_seq - 1])
                next_seq += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for seq in range(base, next_seq):
                    sock.send(packets[seq - 1])
                deadline = time.monotonic() + TIMEOUT
                continue
            sock.settimeout(remaining)
            try:
                ack = read_ack(sock.recv(2048))
            except socket.timeout:
                continue
            if ack is not None and base < ack <= next_seq:
                base = ack
                deadline = time.monotonic() + TIMEOUT

        end_seq = len(packets) + 1
        sock.send(make_packet(1, end_seq))
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            sock.settimeout(deadline - time.monotonic())
            try:
                if read_ack(sock.recv(2048)) == end_seq + 1:
                    break
            except socket.timeout:
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("receiver_ip", help="The IP address of the host that receiver is running on")
    parser.add_argument("receiver_port", type=int, help="The port number on which receiver is listening")
    parser.add_argument("window_size", type=int, help="Maximum number of outstanding packets")
    args = parser.parse_args()
    sender(args.receiver_ip, args.receiver_port, args.window_size)


if __name__ == "__main__":
    main()

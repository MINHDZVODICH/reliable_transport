# CS145 Project 4 — Reliable Transport

## Implementation

Both implementations use UDP, a 16-byte header, CRC32, and payloads of at most
1456 bytes (1472 bytes per RTP datagram). START uses sequence 0 and receives ACK 1;
DATA starts at sequence 1. Binary input and output use the buffered byte streams.

- `RTP-base` buffers out-of-order DATA and sends cumulative ACKs for the next
  expected packet. After 500 ms without window advancement, the sender resends
  all outstanding packets.
- `RTP-opt` acknowledges each DATA sequence separately. The sender tracks ACKs
  and uses one 500 ms timer to resend only unacknowledged packets. Out-of-order
  or duplicate ACKs do not reset this timer unless the window advances.
- Both receivers discard corrupt or malformed packets, enforce the receive
  window, ignore other peers and START during an active connection, and write
  contiguous data exactly once.
- END is sent after all DATA is acknowledged. The sender exits on the matching
  ACK or after 500 ms. A receiver exits after receiving and acknowledging a
  valid END.

## Reproducible tests

The additional suite is `tests/test_transport.py`, using only Python's standard
library as its test runner. The sender, receiver, and supplied proxy require Scapy.
Run from the repository root on Linux:

```sh
python3 -m unittest discover -s tests -v
```

On this Windows machine, the Linux tests can run through WSL:

```powershell
wsl -d Ubuntu-26.04 --exec python3 -m unittest discover -s tests -v
```

To reproduce testing with the assignment's exact Scapy version, run this from
PowerShell in the repository root with Docker running:

```powershell
docker run --rm --mount "type=bind,source=${PWD},target=/work,readonly" -w /work -e PYTHONDONTWRITEBYTECODE=1 python:3.8-slim sh -c "pip install --disable-pip-version-check -q scapy==2.4.0 && python -m unittest discover -s tests -v"
```

The suite has four test methods covering 28 scenarios:

| Coverage | Scenarios |
| --- | ---: |
| Empty/binary transfers of 0, 1, 1455, 1456, 1457 and 10240 bytes, both variants | 12 |
| Original proxy, errors `0123`, windows 1/3/128, seeds 145/146, both variants | 12 |
| Receiver ACKs, buffering, CRC, malformed packets, window limits, foreign peer, duplicate START/DATA | 2 |
| Sender retransmission, reordered/duplicate/corrupt ACKs and missing END ACK | 2 |

The proxy is executed directly from the original `test_scripts/proxy.py` through
`runpy`; only the random seed is set before execution. Its source and algorithms
are unchanged. All transfer outputs are compared byte for byte, not as text.
Each test owns its processes and uses temporary files and dynamically chosen ports.

### Verified results (2026-10-31)

| Environment | Result |
| --- | --- |
| Linux container, Python 3.8, Scapy 2.4.0 (assignment version) | All 4 methods / 28 scenarios passed; 71.979 s |
| Ubuntu 26.04 / WSL, Python 3.14.4, Scapy 2.7.01 | All 4 methods / 28 scenarios passed; 80.401 s |
| Windows, Python 3.14.7, Scapy 2.8.0 | 3 methods passed; Linux-only proxy method skipped; 20.078 s |

No sender/receiver changes were needed after the specification review and tests.
The supplied `test_scripts/`, protocol helpers and assignment README are unchanged.

## Grading notes

The original proxy can drop END itself: for example, the optimized sender with
window 128 and seed 146 produced `Drop. START/END: 18`. All DATA was delivered.
The specification allows the sender to exit after 500 ms, so the receiver can
remain waiting in this case. The suite checks the complete output and cleans up
that receiver; separate tests require a clean receiver exit when END is delivered.

The supplied `test.sh` is unchanged, including its `$SENDER_ID` typo and Linux
tool requirements. The additional runner follows the documented manual testing
path with the original proxy, avoiding sudo, fixed ports and unquoted paths.
On Windows, the original-proxy test is explicitly skipped because
`socket.MSG_DONTWAIT` is unavailable; use Linux to execute the full suite.

`RTP-base/output.txt` and `RTP-opt/output.txt` were regenerated using binary file
handles and checked against the 24248-byte sample input. They are ignored by Git.

Submission tagging/pushing is a separate publishing step. After committing the
version to submit, the assignment specifies:

```sh
git tag -a submission -m "Final Submission"
git push --tags
```

## Sources

- [Original assignment and protocol specification](https://github.com/Harvard-CS145/reliable_transport)
- Original `utils.py` and `test_scripts/` from that repository.
- The additional regression tests and these testing notes were prepared with
  assistance from OpenAI Codex.

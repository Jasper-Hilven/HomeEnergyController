#!/usr/bin/python3
"""Marstek V3 UDP JSON client (v2) with helpers
Communicates with a Marstek V3 battery over UDP port 30000 using JSON payloads.
Helper subcommands:
  - get-device
  - wifi-status (alias: wi-status)
  - ble-status
  - bat-status
  - pv-status
  - es-status
  - es-mode
  - all-status (runs all API calls in sequence)
Defaults:
  Target: 192.168.2.151:30000
  Local bind: 0.0.0.0:30000 (so replies arrive correctly).
"""

import argparse
import json
import logging
import socket
import sys
import time
from typing import Optional, Tuple, List

DEFAULT_IP = "192.168.2.233"
DEFAULT_PORT = 30000

def hexdump(data: bytes) -> str:
    toprint = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hexpart = " ".join(f"{b:02x}" for b in chunk).ljust(16*3)
        asciipart = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        toprint.append(f"{i:08x}  {hexpart} |{asciipart}|")
    return "\n".join(toprint)

def make_socket(local_bind: Optional[Tuple[str, int]] = None, timeout: float = 1.5) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    if local_bind:
        s.bind(local_bind)
    else:
        # Default bind to port 30000 so Marstek replies arrive here
        s.bind(("0.0.0.0", 30000))
    return s

def send_and_receive(
    ip: str,
    port: int,
    payload: dict,
    timeout: float = 1.5,
    retries: int = 2,
    expect_reply: bool = True,
    max_packets: int = 16,
    local_bind: Optional[Tuple[str, int]] = None,
    pretty: bool = True,
    raw: bool = False,
) -> List[bytes]:
    """Send JSON payload and collect replies (raw bytes)."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock = make_socket(local_bind=local_bind, timeout=timeout)
    addr = (ip, port)
    responses: List[bytes] = []

    for attempt in range(retries + 1):
        # logging.debug("Sending (%d/%d) to %s:%d: %s", attempt+1, retries+1, ip, port, data)
        try:
            sock.sendto(data, addr)
        except Exception as e:
            logging.error("Failed to send: %s", e)
            if attempt == retries:
                raise
            continue

        if not expect_reply:
            return []

        start = time.monotonic()
        packets = 0
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                pkt, fromaddr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except Exception as e:
                logging.error("Receive error: %s", e)
                break
            packets += 1
            logging.debug("Received %d bytes from %s:%d", len(pkt), *fromaddr)
            responses.append(pkt)
            if packets >= max_packets:
                logging.debug("Reached max_packets=%d, stopping receive loop", max_packets)
                break

        if responses:
            break
        else:
            logging.debug("No reply, retrying...")

    for i, pkt in enumerate(responses, 1):
        # print(f"--- Packet {i} ({len(pkt)} bytes) ---")
        if raw:
            print(hexdump(pkt))
        else:
            try:
                obj = json.loads(pkt.decode("utf-8", errors="strict"))
                if pretty:
                    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))
                else:
                    print(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
            except Exception:
                print(hexdump(pkt))
                print("\n(Text)\n" + pkt.decode("utf-8", errors="replace"))
    return responses

def load_payload(args) -> dict:
    sources = [args.json is not None, args.file is not None, args.stdin]
    if sum(1 for s in sources if s) != 1:
        raise SystemExit("Provide exactly one of --json, --file, or --stdin")
    if args.json is not None:
        return json.loads(args.json)
    if args.file is not None:
        with open(args.file, "r", encoding="utf-8") as f:
            return json.load(f)
    text = sys.stdin.read()
    return json.loads(text)

def cli():
    p = argparse.ArgumentParser(description="UDP JSON client for Marstek V3 battery")
    p.add_argument("--ip", default=DEFAULT_IP, help=f"Target IP (default: {DEFAULT_IP})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Target UDP port (default: {DEFAULT_PORT})")
    p.add_argument("--timeout", type=float, default=1.5, help="Receive timeout per attempt in seconds (default: 1.5)")
    p.add_argument("--retries", type=int, default=2, help="Number of retries on no reply (default: 2)")
    p.add_argument("--bind", default=None, help="Bind to local ip:port (e.g. 0.0.0.0:30000)")
    p.add_argument("--no-pretty", action="store_true", help="Do not pretty-print JSON replies")
    p.add_argument("--raw", action="store_true", help="Print raw hex dump instead of JSON decoding")
    p.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity (-v, -vv)")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp_send = sub.add_parser("send", help="Send an arbitrary JSON payload")
    src = sp_send.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="Inline JSON string to send")
    src.add_argument("--file", help="Path to file containing JSON to send")
    src.add_argument("--stdin", action="store_true", help="Read JSON from stdin")
    sp_send.add_argument("--no-reply", action="store_true", help="Do not wait for a reply")
    sp_send.add_argument("--max-packets", type=int, default=16, help="Max packets to collect")

    sp_status = sub.add_parser("status", help="Send a placeholder 'status' request")
    sp_status.add_argument("--key", default="cmd", help="Key name to use (default: cmd)")
    sp_status.add_argument("--value", default="read_status", help="Value to use (default: read_status)")

    sp_getdev = sub.add_parser("get-device", help="Send Marstek.GetDevice request")
    sp_getdev.add_argument("--ble-mac", default="0", help="BLE MAC address (default: 0)")

    # Wifi (correct) + alias 'wi-status' for convenience
    sp_wifi = sub.add_parser("wifi-status", help="Send Wifi.GetStatus request")
    sp_wifi.add_argument("--id", type=int, default=0, help="Device id (default: 0)")
    sp_wi = sub.add_parser("wi-status", help="[alias] Send Wifi.GetStatus request")
    sp_wi.add_argument("--id", type=int, default=0, help="Device id (default: 0)")

    sp_ble = sub.add_parser("ble-status", help="Send BLE.GetStatus request")
    sp_ble.add_argument("--id", type=int, default=0, help="Device id (default: 0)")

    sp_bat = sub.add_parser("bat-status", help="Send Bat.GetStatus request")
    sp_bat.add_argument("--id", type=int, default=0, help="Device id (default: 0)")

    sp_pv = sub.add_parser("pv-status", help="Send PV.GetStatus request")
    sp_pv.add_argument("--id", type=int, default=0, help="Device id (default: 0)")

    sp_es = sub.add_parser("es-status", help="Send ES.GetStatus request")
    sp_es.add_argument("--id", type=int, default=0, help="Device id (default: 0)")

    sp_mode = sub.add_parser("es-mode", help="Send ES.GetMode request")
    sp_mode.add_argument("--id", type=int, default=0, help="Device id (default: 0)")

    sub.add_parser("all-status", help="Run all status API calls in sequence")
    sub.add_parser("test-some", help="Run a few test API calls")
    args = p.parse_args()

    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    local_bind = None
    if args.bind:
        host, port_str = args.bind.rsplit(":", 1)
        local_bind = (host, int(port_str))

    if args.cmd == "send":
        payload = load_payload(args)
        expect_reply = not args.no_reply
        # print(f"Sending payload: {payload}", file=sys.stderr)
        send_and_receive(
            args.ip, args.port, payload,
            timeout=args.timeout, retries=args.retries,
            expect_reply=expect_reply, local_bind=local_bind,
            pretty=not args.no_pretty, raw=args.raw,
        )
        return

    if args.cmd == "status":
        payload = {args.key: args.value}
        expect_reply = True
        # print(f"Sending placeholder status: {payload}", file=sys.stderr)
        send_and_receive(
            args.ip, args.port, payload,
            timeout=args.timeout, retries=args.retries,
            expect_reply=expect_reply, local_bind=local_bind,
            pretty=not args.no_pretty, raw=args.raw,
        )
        return

    if args.cmd == "get-device":
        payload = {"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": args.ble_mac}}
        # print(f"Sending {payload['method']}: {payload}", file=sys.stderr)
        send_and_receive(args.ip, args.port, payload, timeout=args.timeout,
                         retries=args.retries, expect_reply=True, local_bind=local_bind,
                         pretty=not args.no_pretty, raw=args.raw)
        return

    if args.cmd in ("wifi-status", "wi-status"):
        payload = {"id": 1, "method": "Wifi.GetStatus", "params": {"id": args.id}}
    elif args.cmd == "ble-status":
        payload = {"id": 1, "method": "BLE.GetStatus", "params": {"id": args.id}}
    elif args.cmd == "bat-status":
        payload = {"id": 1, "method": "Bat.GetStatus", "params": {"id": args.id}}
    elif args.cmd == "pv-status":
        payload = {"id": 1, "method": "PV.GetStatus", "params": {"id": args.id}}
    elif args.cmd == "es-status":
        payload = {"id": 1, "method": "ES.GetStatus", "params": {"id": args.id}}
    elif args.cmd == "es-mode":
        payload = {"id": 1, "method": "ES.GetMode", "params": {"id": args.id}}
    elif args.cmd == "all-status":
        calls = [
            {"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}},
            {"id": 1, "method": "Wifi.GetStatus", "params": {"id": 0}},
            {"id": 1, "method": "BLE.GetStatus", "params": {"id": 0}},
            {"id": 1, "method": "Bat.GetStatus", "params": {"id": 0}},
            {"id": 1, "method": "PV.GetStatus", "params": {"id": 0}},
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}},
            {"id": 1, "method": "ES.GetMode", "params": {"id": 0}},
        ]
        for payload in calls:
            # print(f"Sending {payload['method']}: {payload}", file=sys.stderr)
            send_and_receive(args.ip, args.port, payload,
                             timeout=args.timeout, retries=args.retries,
                             expect_reply=True, local_bind=local_bind,
                             pretty=not args.no_pretty, raw=args.raw)
        return
    elif args.cmd == "test-some":
        calls = [
            # original attempts
            {
                "id": 1,
                "method": "ES.SetMode",
                "params": {
                    "id": 0,
                    "config": {
                        "mode": "Auto",
                        "auto_cfg": {
                            "enable": 1
                        }
                    }
                }
            },
            {"id": 1, "method": "ES.GetMode", "params": {"id": 0}},
        ]
        for payload in calls:
            # print(f"Sending {payload['method']}: {payload}", file=sys.stderr)
            send_and_receive(args.ip, args.port, payload,
                             timeout=args.timeout, retries=args.retries,
                             expect_reply=True, local_bind=local_bind,
                             pretty=not args.no_pretty, raw=args.raw)
        return
    else:
        p.error("Unknown command")

    # print(f"Sending {payload['method']}: {payload}", file=sys.stderr)
    send_and_receive(
        args.ip, args.port, payload,
        timeout=args.timeout, retries=args.retries,
        expect_reply=True, local_bind=local_bind,
        pretty=not args.no_pretty, raw=args.raw,
    )

if __name__ == "__main__":
    try:
        cli()
    except KeyboardInterrupt:
        pass
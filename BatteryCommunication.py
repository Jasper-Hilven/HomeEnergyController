#!/usr/bin/python3

import json
import socket
import time
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_PORT = 30000

def make_socket(local_bind: Optional[Tuple[str, int]] = None, timeout: float = 1.5) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    s.bind(local_bind or ("0.0.0.0", 30000))
    return s

def send_and_receive(ip: str, port: int, payload: dict, timeout: float = 1.5, retries: int = 2) -> List[bytes]:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock = make_socket(timeout=timeout)
    addr = (ip, port)
    responses: List[bytes] = []

    for attempt in range(retries + 1):
        sock.sendto(data, addr)
        start = time.monotonic()
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                pkt, _ = sock.recvfrom(65535)
                responses.append(pkt)
            except socket.timeout:
                break
        if responses:
            break
    return responses


def get_battery_status(ip: str) -> dict:
    payload = {"id": 1, "method": "ES.GetMode", "params": {"id": 0}}
    responses = send_and_receive(ip, DEFAULT_PORT, payload)

    for pkt in responses:
        try:
            obj = json.loads(pkt.decode("utf-8"))
            return obj  # ✅ Return parsed JSON directly
        except json.JSONDecodeError:
            return {
                "error": "Invalid JSON response",
                "raw_text": pkt.decode("utf-8", errors="replace")
            }

    # If no response packets were received
    return {"error": "No response from device"}


def get_all_battery_statuses(ips: List[str], retries: int = 3, delay: float = 1.5) -> dict:
    """
    Requests battery status from multiple IPs with retries.

    :param ips: List of IP addresses.
    :param retries: Number of retries per IP.
    :param delay: Delay in seconds between retries.
    :return: Dict mapping IPs to results.
    """
    results = {}

    for ip in ips:
        print(f"Requesting status from {ip}...")
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                result = get_battery_status(ip)

                # Consider a response with an "error" key as a failed attempt
                if "error" not in result:
                    results[ip] = result
                    break
                else:
                    last_error = result["error"]
                    print(f"Attempt {attempt}/{retries} failed for {ip}: {last_error}")

            except Exception as e:
                last_error = str(e)
                print(f"Attempt {attempt}/{retries} threw exception for {ip}: {last_error}")

            if attempt < retries:
                time.sleep(delay)

        # If all retries failed, record the last error
        if ip not in results:
            results[ip] = {"error": f"Failed after {retries} retries: {last_error}"}

    return results


def set_battery_status(ip: str, is_auto: bool, manual_power: Optional[int] = 0):
    """
    Send a control command to a battery to set it in Auto or Manual mode.

    Args:
        ip: IP address of the battery.
        is_auto: True to set to automatic mode, False for manual.
        manual_power: Power level for manual mode (-2500 to 2500).

    Returns:
        Parsed JSON response or an error dict.
    """

    if is_auto:
        payload = {
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
        }
    else:
        # Ensure power within safe range
        power = max(-2500, min(2500, int(manual_power or 0)))
        payload = {
            "id": 1,
            "method": "ES.SetMode",
            "params": {
                "id": 0,
                "config": {
                    "mode": "Manual",
                    "manual_cfg": {
                        "time_num": 1,
                        "start_time": "00:00",
                        "end_time": "23:59",
                        "week_set": 127,
                        "power": power,
                        "enable": 1
                    }
                }
            }
        }

    try:
        responses = send_and_receive(ip, DEFAULT_PORT, payload)
        for pkt in responses:
            try:
                return json.loads(pkt.decode("utf-8"))
            except json.JSONDecodeError:
                continue
        return {"error": "Invalid or empty response", "ip": ip}
    except Exception as e:
        return {"error": str(e), "ip": ip}
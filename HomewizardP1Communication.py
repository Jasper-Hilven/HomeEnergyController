import requests

def get_current_power(ip: str, port: int = 80) -> float:
    url = f"http://{ip}:{port}/api/v1/data"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    power = data.get("active_power_w")
    if power is None:
        raise KeyError(f"No ‘active_power_w’ field in response: {data}")
    return float(power)
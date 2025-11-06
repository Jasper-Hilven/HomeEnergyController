# Home Energy Controller

This project orchestrates home battery systems based on live grid power usage from a HomeWizard P1 meter. It discovers/queries batteries over UDP, decides per-battery operating mode (Auto vs Manual) and power setpoints, and applies those settings periodically.

It consists of four modules:
- BatteryCommunication.py — UDP JSON client for querying and controlling batteries.
- HomewizardP1Communication.py — HTTP client to read current power from a HomeWizard P1 smart-meter dongle.
- EnergyController.py — Core decision engine computing how batteries should operate.
- MyEnergyController.py — Orchestrator that ties inputs and outputs together and runs a loop.

Note: IP addresses are currently hard-coded for simplicity. See “Configuration” to adapt to your setup.


## How it works
1. Query each battery for status over UDP (port 30000) using ES.GetMode.
2. Fetch current active power (W) from HomeWizard P1: http://<P1_IP>:80/api/v1/data (expects JSON with `active_power_w`).
3. Translate raw battery status into a normalized structure (charge, current mode, effective power, ip).
4. Run decision logic (EnergyController.control_energy_flow) to choose:
   - Which battery (if any) should be in Auto mode to handle net load/export.
   - What manual power setpoints other batteries should use.
5. Apply decisions to each battery with ES.SetMode (Auto or Manual with set power).
6. Repeat every minute (in main loop), or run once for testing.


## Requirements
- Python 3.8+ (tested with 3.10+ recommended)
- Network access to:
  - Your batteries on UDP port 30000
  - HomeWizard P1 on HTTP port 80
- Python packages:
  - requests

Install dependency:
```
pip install requests
```


## Configuration
Edit MyEnergyController.singleRun() to match your environment:
- Battery IPs: update the `ips` list.
- HomeWizard P1 IP: update the IP passed to `HomewizardP1Communication.get_current_power("<P1_IP>")`.

Defaults in code:
- Battery UDP port: 30000 (see DEFAULT_PORT in BatteryCommunication.py)
- HomeWizard endpoint: `http://<ip>:80/api/v1/data`

Safety limits:
- When setting manual power, values are clamped to [-2500, 2500] W before sending.


## Running
From the project directory:

- Single run for testing (executes one control cycle and prints decisions):
```
python3 MyEnergyController.py
```
This script will:
- Print raw battery statuses (as received JSON per IP or error).
- Print parsed batteries list used for decision making.
- Print P1Usage (positive=import from grid, negative=export to grid).
- Print the computed control decision.
- Send commands to batteries according to the decision and print what it set.

- Continuous control loop (runs every minute):
`MyEnergyController.py` already defaults to `main_loop()` when executed as `__main__`. It prints a timestamp each cycle and sleeps 60s between runs. Stop with Ctrl+C.


## Module reference (brief)

### BatteryCommunication.py
- `get_battery_status(ip: str) -> dict`: Sends ES.GetMode and returns parsed JSON or an error dict.
- `get_all_battery_statuses(ips: List[str]) -> dict`: Convenience to query multiple IPs with basic error handling.
- `set_battery_status(ip: str, is_auto: bool, manual_power: Optional[int] = 0) -> dict`: Sends ES.SetMode.
  - Auto payload enables auto_cfg.
  - Manual payload sets a schedule covering 00:00–23:59, week_set=127, with desired power (clamped).
- Internals use UDP with JSON and a small receive window per request with retries.

### HomewizardP1Communication.py
- `get_current_power(ip: str, port: int = 80) -> float`: GETs `/api/v1/data` and returns `active_power_w` as float. Raises on HTTP error or missing field.

### EnergyController.py
- `control_energy_flow(batteries, carState, P1Usage, now=None) -> dict`: Pure decision logic. Expects `batteries` list of dicts (with keys like `charge`, `isManual`, `isAutomatic`, `effectivePower`, `ip`), a `carState` dict, and current net power `P1Usage`.
  - Internally computes which battery can handle the effective grid load/export in Auto mode, possibly reassigning which device is Auto.
  - Assigns manual power setpoints to the other batteries, enforcing boundaries.
  - Returns a dict with updated batteries entries (including `manualSetPower`, `isAutomatic`, etc.).

### MyEnergyController.py
- `singleRun()` performs one full cycle: read states, decide, apply.
- `main_loop()` repeats `singleRun()` every 60 seconds with basic exception handling.


## Troubleshooting
- No response from device: Ensure UDP 30000 is reachable and the IPs are correct. Firewalls may block UDP.
- P1 JSON missing `active_power_w`: Verify your HomeWizard P1 firmware/API. The code raises a KeyError with the raw JSON.
- Commands applied but no effect: Some batteries may require authentication or a different payload schema. This code assumes an open ES.GetMode/ES.SetMode interface as implemented by your device.
- Timeouts or flaky network: Increase `timeout` or `retries` in `BatteryCommunication.send_and_receive` if needed.


## Notes & Safety
- Use at your own risk. Sending incorrect power setpoints can damage equipment or violate grid connection rules.
- The project does not implement authentication or encryption; traffic is plaintext over LAN.
- Validate device-specific JSON schemas before running unattended.


## License
Add your preferred license here.

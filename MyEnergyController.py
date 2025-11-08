import time
from datetime import datetime
import json

import BatteryCommunication
import HomewizardP1Communication
from EnergyController import control_energy_flow


def singleRun():
    ips = ["192.168.2.108", "192.168.2.147", "192.168.2.233"]

    statuses = BatteryCommunication.get_all_battery_statuses(ips)

    errors = {ip: data for ip, data in statuses.items() if "error" in data}

    if errors:
        print("[ERROR] One or more batteries reported communication issues:")
        for ip, data in errors.items():
            print(f"  - {ip}: {data['error']}")

    batteries = []
    for ip, status in statuses.items():
        try:
            res = status["result"]
            bat_soc = res.get("bat_soc")
            mode = res.get("mode", "Auto")

            off = res.get("offgrid_power", 0) or 0
            off = off if (10000 >= off >= -10000) else 0
            on = res.get("ongrid_power", 0) or 0
            on = on if (10000 >= on >= -10000) else 0
            effective_power = off if off != 0 else (on if on != 0 else 0)

            batteries.append({
                "charge": bat_soc if bat_soc is not None else 0,
                "isManual": mode.lower() == "manual",
                "manualSetPower": 0,
                "isAutomatic": mode.lower() == "auto",
                "effectivePower": effective_power,
                "ip": ip
            })
        except Exception as e:
            print(f"⚠️ Error parsing battery {ip}: {e}")
    print("....batteries....")
    print(json.dumps(batteries, indent=2, sort_keys=True))
    carState = {
        "IsCarConnected": False,
        "CarIntendedPowerUsage": 0,
        "CarPowerUsage": 0
    }

    P1Usage = HomewizardP1Communication.get_current_power("192.168.2.192")
    print("\n===P1Usage===")
    print(P1Usage)

    control = control_energy_flow(batteries, carState, P1Usage, datetime.now())

    print("\n=== Control Decision ===")
    print(json.dumps(control, indent=2, sort_keys=True))

    # --- APPLY CONTROL DECISIONS ---
    print("\n=== Applying control decisions ===")
    for bat in control["batteries"]:
        ip = bat["ip"]
        if bat["isAutomatic"]:
            print(f"[{ip}] → Setting to AUTO")
            BatteryCommunication.set_battery_status(ip, is_auto=True)
        elif bat["isManual"]:
            print(f"[{ip}] → Setting to MANUAL with power {bat['manualSetPower']} W")
            BatteryCommunication.set_battery_status(ip, is_auto=False, manual_power=bat["manualSetPower"])
        else:
            print(f"[{ip}] → Setting to MANUAL with power {bat['manualSetPower']} W")
            BatteryCommunication.set_battery_status(ip, is_auto=False, manual_power=0)

    print("\n✅ All commands applied.")

    return control

def main_loop():
    """Run singleRun() every minute indefinitely."""
    print("=== Starting continuous control loop (1-minute interval) ===")
    try:
        while True:
            print(f"\n\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
            singleRun()
            print("Sleeping for 60 seconds...\n")
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")


if __name__ == "__main__":
    main_loop()
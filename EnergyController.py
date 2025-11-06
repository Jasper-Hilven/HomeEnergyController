from datetime import datetime
from typing import List, Dict, Optional


def control_energy_flow(
    batteries: List[Dict],
    carState: Dict,
    P1Usage: int,
    now: Optional[datetime] = None
) -> Dict:
    """
    Decide battery and car control states to minimize grid usage,
    charge efficiently, and respect constraints.
    """

    if now is None:
        now = datetime.now()

    # ---- Determine time context ----
    weekday = now.weekday() < 5  # Mon–Fri
    peak_hours = weekday and (7 <= now.hour < 22)
    low_hours = not peak_hours

    # Copy to avoid mutation
    bat_out = [
        {
            "charge": b["charge"],
            "isManual": b["isManual"],
            "manualSetPower": b["manualSetPower"],
            "isAutomatic": b["isAutomatic"],
            "ip": b["ip"],
            "effectivePower": b.get("effectivePower", 0)
        }
        for b in batteries
    ]

    # ----------------------------- #
    # ---- Helper sub-functions ----
    # ----------------------------- #

    def avg_charge() -> float:
        return sum(b["charge"] for b in batteries) / len(batteries)

    def all_above(level: int) -> bool:
        return all(b["charge"] >= level for b in batteries)

    def all_below(level: int) -> bool:
        return all(b["charge"] <= level for b in batteries)

    def get_auto_candidate(effective_p1: int) -> int:
        """Select ideal automatic battery depending on grid direction."""
        valid_idxs = range(len(batteries))

        if effective_p1 > 100:
            # Need to discharge → pick highest-charge battery
            candidates = [i for i in valid_idxs if batteries[i]["charge"] > 20]
            if not candidates:
                candidates = list(valid_idxs)
            return max(candidates, key=lambda i: batteries[i]["charge"])

        elif effective_p1 < -100:
            # Need to charge → pick lowest-charge battery
            candidates = [i for i in valid_idxs if batteries[i]["charge"] < 95]
            if not candidates:
                candidates = list(valid_idxs)
            return min(candidates, key=lambda i: batteries[i]["charge"])

        else:
            # Neutral → mid-level
            return sorted(valid_idxs, key=lambda i: batteries[i]["charge"])[len(batteries) // 2]

    def select_auto_battery(effective_p1: int, soc_threshold: int = 5) -> int:
        """Select the best automatic battery considering old auto state."""
        current_auto_idx = next((i for i, b in enumerate(batteries) if b.get("isAutomatic")), None)
        candidate_idx = get_auto_candidate(effective_p1)

        if current_auto_idx is None:
            return candidate_idx

        old_auto = batteries[current_auto_idx]
        new_auto = batteries[candidate_idx]
        diff = abs(new_auto["charge"] - old_auto["charge"])

        # Keep old auto if SoC difference small and within safe range
        if diff < soc_threshold and 20 < old_auto["charge"] < 95:
            return current_auto_idx

        # Switch if old auto out of safe SoC
        if old_auto["charge"] <= 20 or old_auto["charge"] >= 95:
            return candidate_idx

        # For small imbalance, keep old to avoid unnecessary switching
        if abs(effective_p1) < 1500:
            return current_auto_idx

        return candidate_idx

    def compute_car_intent(P1Usage: int) -> int:
        """Determine car power behavior."""
        if not carState.get("IsCarConnected", False):
            return 0
        if P1Usage < 0:
            # Solar surplus
            power = int(round(abs(P1Usage) * 0.85))
            if all_above(90):
                power = int(round(abs(P1Usage)))
        else:
            # Drawing from grid
            if low_hours and all_above(90):
                power = 1400
            elif low_hours and not all_below(20):
                power = 1400
            else:
                power = 0
        return power

    def can_auto_handle(effective_p1: int, old_auto: Optional[Dict], new_auto: Dict) -> bool:
        """Check if old or new auto battery can handle imbalance."""
        def remaining_capacity(bat: Dict) -> float:
            return max(0, 2500 - abs(bat.get("effectivePower", 0)))

        caps = []
        if old_auto:
            caps.append(remaining_capacity(old_auto))
        if new_auto:
            caps.append(remaining_capacity(new_auto))

        max_cap = max(caps) if caps else 0
        return abs(effective_p1) <= (1500 + max_cap / 2)

    def assign_manual_powers(auto_idx: int, effective_p1: int, old_auto_idx: Optional[int]):
        """Assign manualSetPower to additional batteries if imbalance is large."""
        direction = -1 if effective_p1 > 0 else 1  # +1 charge, -1 discharge
        need_power = min(2500, abs(effective_p1)) * direction

        auto_bat = bat_out[auto_idx]
        old_auto = bat_out[old_auto_idx] if old_auto_idx is not None else None

        # --- Check if auto (or old auto) can handle the load ---
        if can_auto_handle(effective_p1, old_auto, auto_bat):
            for i, b in enumerate(bat_out):
                if i != auto_idx:
                    b["isManual"] = False
                    b["manualSetPower"] = 0
                    b["isAutomatic"] = False
            return

        # --- Otherwise, assign manual helpers ---
        high = max(b["charge"] for b in bat_out)
        low = min(b["charge"] for b in bat_out)
        diff = high - low
        overload = abs(effective_p1) - 1500
        scale = min(1.0, overload / 1000.0)

        order = (
            sorted(range(len(bat_out)), key=lambda i: bat_out[i]["charge"])
            if direction > 0
            else sorted(range(len(bat_out)), key=lambda i: bat_out[i]["charge"], reverse=True)
        )

        for i in order:
            if i == auto_idx:
                continue
            b = bat_out[i]

            if (direction < 0 and b["charge"] <= 20) or (direction > 0 and b["charge"] >= 100):
                b["isManual"] = False
                b["manualSetPower"] = 0
                b["isAutomatic"] = False
                continue

            b["isManual"] = True
            b["isAutomatic"] = False

            if diff > 10:
                bias = abs(b["charge"] - avg_charge()) / max(1, diff)
                b["manualSetPower"] = int(round(need_power * (0.5 + 0.5 * bias) * scale))
            else:
                b["manualSetPower"] = int(round(need_power * scale))

            b["manualSetPower"] = max(-2500, min(2500, b["manualSetPower"]))

        # Small-power cutoff
        for b in bat_out:
            if b.get("isManual") and abs(b["manualSetPower"]) < 300:
                b["isManual"] = False
                b["manualSetPower"] = 0

    def enforce_boundaries():
        """Ensure batteries stay within safe SoC limits."""
        for b in bat_out:
            if b["charge"] >= 100 and b["manualSetPower"] > 0:
                b["manualSetPower"] = 0
                b["isManual"] = False
            if b["charge"] <= 20 and b["manualSetPower"] < 0:
                b["manualSetPower"] = 0
                b["isManual"] = False

    # -------------------------- #
    # ---- Main control flow ----
    # -------------------------- #

    car_intended = compute_car_intent(P1Usage)
    effective_p1 = P1Usage + car_intended

    old_auto_idx = next((i for i, b in enumerate(batteries) if b.get("isAutomatic")), None)
    auto_idx = select_auto_battery(effective_p1)
    auto_bat = bat_out[auto_idx]
    auto_bat["isAutomatic"] = True
    auto_bat["isManual"] = False
    auto_bat["manualSetPower"] = 0

    assign_manual_powers(auto_idx, effective_p1, old_auto_idx)
    enforce_boundaries()

    return {
        "batteries": bat_out,
        "carState": {"CarIntendedPowerUsage": car_intended}
    }

from datetime import datetime
from typing import List, Dict, Optional


# -------------------------------------------------------
# -------- Utility functions for state evaluation --------
# -------------------------------------------------------

def avg_charge(bats: List[Dict]) -> float:
    """Average state of charge across all batteries."""
    return sum(b["charge"] for b in bats) / len(bats) if bats else 0.0


def all_above(bats: List[Dict], level: int) -> bool:
    """True if all batteries are above given SoC."""
    return all(b["charge"] >= level for b in bats)


def all_below(bats: List[Dict], level: int) -> bool:
    """True if all batteries are below given SoC."""
    return all(b["charge"] <= level for b in bats)


def remaining_capacity(bat: Dict) -> float:
    """Estimate remaining usable power headroom."""
    return max(0, 2500 - abs(bat.get("effectivePower", 0)))


# -------------------------------------------------------
# -------- Battery selection and control helpers --------
# -------------------------------------------------------

def get_auto_candidate(batteries: List[Dict], effective_p1: int) -> int:
    """
    Select ideal automatic battery depending on grid direction.
    Considers charge level and optional efficiency.
    """
    valid_idxs = range(len(batteries))

    # Efficiency weighting (lower resistance or higher efficiency = more desirable)
    def weighted_charge(bat: Dict) -> float:
        eff = bat.get("efficiency", 1.0)
        ir = bat.get("internalResistance", 0.0)
        # Favor higher efficiency and lower resistance
        return bat["charge"] * eff * (1.0 + (0.1 if ir == 0 else 1 / (1 + ir)))

    if effective_p1 > 100:
        # Need to discharge → pick highest-charge + best efficiency
        candidates = [i for i in valid_idxs if batteries[i]["charge"] > 20]
        if not candidates:
            candidates = list(valid_idxs)
        return max(candidates, key=lambda i: weighted_charge(batteries[i]))

    elif effective_p1 < -100:
        # Need to charge → pick lowest-charge + best efficiency
        candidates = [i for i in valid_idxs if batteries[i]["charge"] < 95]
        if not candidates:
            candidates = list(valid_idxs)
        return min(candidates, key=lambda i: weighted_charge(batteries[i]))

    else:
        # Neutral → mid-level battery by charge
        return sorted(valid_idxs, key=lambda i: batteries[i]["charge"])[len(batteries) // 2]


def select_auto_battery(batteries: List[Dict], effective_p1: int, soc_threshold: int = 5) -> int:
    """Select the best automatic battery considering the previous auto and SoC."""
    current_auto_idx = next((i for i, b in enumerate(batteries) if b.get("isAutomatic")), None)
    candidate_idx = get_auto_candidate(batteries, effective_p1)

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


def compute_car_intent(carState: Dict, P1Usage: int, low_hours: bool, batteries: List[Dict]) -> int:
    """Determine car charging/discharging intent."""
    if not carState.get("IsCarConnected", False):
        return 0

    if P1Usage < 0:
        # Solar surplus
        power = int(round(abs(P1Usage) * 0.85))
        if all_above(batteries, 90):
            power = int(round(abs(P1Usage)))
    else:
        # Drawing from grid
        if low_hours and all_above(batteries, 90):
            power = 1400
        elif low_hours and not all_below(batteries, 20):
            power = 1400
        else:
            power = 0
    return power


def can_auto_handle(effective_p1: int, old_auto: Optional[Dict], new_auto: Dict) -> bool:
    """Check if old or new auto battery can handle the imbalance alone."""
    caps = []
    if old_auto:
        caps.append(remaining_capacity(old_auto))
    if new_auto:
        caps.append(remaining_capacity(new_auto))

    max_cap = max(caps) if caps else 0
    return abs(effective_p1) <= (1500 + max_cap / 2)


def assign_manual_powers(bat_out: List[Dict], auto_idx: int, effective_p1: int, old_auto_idx: Optional[int]):
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
        sorted(range(len(bat_out)), key=lambda i: bat_out[i]["charge"]) if direction > 0 else
        sorted(range(len(bat_out)), key=lambda i: bat_out[i]["charge"], reverse=True)
    )

    for i in order:
        if i == auto_idx:
            continue
        b = bat_out[i]

        # Skip unsafe batteries
        if (direction < 0 and b["charge"] <= 20) or (direction > 0 and b["charge"] >= 100):
            b["isManual"] = False
            b["manualSetPower"] = 0
            b["isAutomatic"] = False
            continue

        b["isManual"] = True
        b["isAutomatic"] = False

        if diff > 10:
            bias = abs(b["charge"] - avg_charge(bat_out)) / max(1, diff)
            b["manualSetPower"] = int(round(need_power * (0.5 + 0.5 * bias) * scale))
        else:
            b["manualSetPower"] = int(round(need_power * scale))

        b["manualSetPower"] = max(-2500, min(2500, b["manualSetPower"]))

    # Small-power cutoff
    for b in bat_out:
        if b.get("isManual") and abs(b["manualSetPower"]) < 300:
            b["isManual"] = False
            b["manualSetPower"] = 0


def enforce_boundaries(bat_out: List[Dict]):
    """Ensure batteries stay within safe SoC limits."""
    for b in bat_out:
        if b["charge"] >= 100 and b["manualSetPower"] > 0:
            b["manualSetPower"] = 0
            b["isManual"] = False
        if b["charge"] <= 20 and b["manualSetPower"] < 0:
            b["manualSetPower"] = 0
            b["isManual"] = False


# -------------------------------------------------------
# ----------- Main orchestration controller --------------
# -------------------------------------------------------

def control_energy_flow(
    batteries: List[Dict],
    carState: Dict,
    P1Usage: int,
    now: Optional[datetime] = None
) -> Dict:
    """
    Decide battery and car control states to minimize grid usage,
    charge efficiently, and respect constraints.

    Selection logic:
      Auto battery is chosen based on P1Usage + car_intended + sum(all battery flows)
      with efficiency and SoC weighting.
    """

    if now is None:
        now = datetime.now()

    # ---- Determine time context ----
    weekday = now.weekday() < 5  # Mon–Fri
    peak_hours = weekday and (7 <= now.hour < 22)
    low_hours = not peak_hours

    # Copy to avoid mutating input
    bat_out = [
        {
            "charge": b["charge"],
            "isManual": b.get("isManual", False),
            "manualSetPower": b.get("manualSetPower", 0),
            "isAutomatic": b.get("isAutomatic", False),
            "ip": b.get("ip", ""),
            "effectivePower": b.get("effectivePower", 0),
            "efficiency": b.get("efficiency", 1.0),
            "internalResistance": b.get("internalResistance", 0.0)
        }
        for b in batteries
    ]

    # ----------------------------- #
    # ---- Begin main logic ----
    # ----------------------------- #

    # 1️⃣ Determine car charging/discharging intent
    car_intended = compute_car_intent(carState, P1Usage, low_hours, bat_out)

    # 2️⃣ Compute net P1 usage including car + battery flows
    total_bat_flow = sum(b["effectivePower"] for b in bat_out)
    effective_p1 = P1Usage + car_intended + total_bat_flow

    # 3️⃣ Identify auto battery
    old_auto_idx = next((i for i, b in enumerate(batteries) if b.get("isAutomatic")), None)
    auto_idx = select_auto_battery(bat_out, effective_p1)
    auto_bat = bat_out[auto_idx]
    auto_bat["isAutomatic"] = True
    auto_bat["isManual"] = False
    auto_bat["manualSetPower"] = 0

    # 4️⃣ Adjust manual helper batteries if imbalance too high
    assign_manual_powers(bat_out, auto_idx, effective_p1, old_auto_idx)

    # 5️⃣ Enforce SoC safety limits
    enforce_boundaries(bat_out)

    # ----------------------------- #
    # ---- Build result payload ----
    # ----------------------------- #
    return {
        "batteries": bat_out,
        "carState": {
            "CarIntendedPowerUsage": car_intended
        },
        "debug": {
            "effective_p1": effective_p1,
            "old_auto_idx": old_auto_idx,
            "new_auto_idx": auto_idx,
            "low_hours": low_hours
        }
    }

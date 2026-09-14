from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

def validate_semantics(netlist: str, intent: dict) -> list[str]:
    """
    Validate that the generated netlist contains the physical variables
    and intent constraints requested by the Copilot.
    """
    errors: list[str] = []
    
    # 1. Check for VDD
    vdd = intent.get("vdd")
    if vdd is not None:
        expected = f"{float(vdd):g}"
        netlist_upper = netlist.upper()
        if "VDD" not in netlist_upper:
            errors.append("Missing VDD node or source.")
        elif expected not in netlist_upper:
            # We look for the exact string, though it might be formatted differently
            # For now, just a loose check to see if the value is in the netlist
            if str(vdd) not in netlist_upper and expected not in netlist_upper:
                errors.append(f"Expected VDD≈{expected} value not found in netlist.")

    # 2. Check that all design variables are actually used in the netlist
    for var in intent.get("design_variables", []):
        name = var["name"]
        if f"{{{name}}}" not in netlist and f"{name}" not in netlist:
            errors.append(f"Design variable '{name}' is never used in the netlist.")

    return errors


#!/usr/bin/env python3

import sys
import yaml

source, destination = sys.argv[1:3]

with open(source, encoding="utf-8") as f:
    data = yaml.safe_load(f)

layers = data["layers"]
base = layers["qwerty"]

# Actual physical positions from KEYS_L and KEYS_R
LEFT = {
    0, 1, 2, 3, 4, 5,
    12, 13, 14, 15, 16, 17,
    24, 25, 26, 27, 28, 29,
}

RIGHT = {
    6, 7, 8, 9, 10, 11,
    18, 19, 20, 21, 22, 23,
    32, 33, 34, 35, 36, 37,
}

LEFT_LAYERS = [
    ("Symbol", "tl"),
    ("Number", "tr"),
    ("Function", "bl"),
]

RIGHT_LAYERS = [
    ("Navigation", "tl"),
    ("Mouse", "tr"),
    ("Media", "bl"),
]


def get_label(key):
    """Extract the tap/legend label from a parsed key."""
    if isinstance(key, dict):
        if key.get("type") in ("trans", "held"):
            return None
        return key.get("t")
    return key


if len(base) != 46:
    raise ValueError(f"Expected 46 base keys, found {len(base)}")

for layer_name, _ in LEFT_LAYERS + RIGHT_LAYERS:
    if len(layers[layer_name]) != 46:
        raise ValueError(f"Layer {layer_name} does not contain 46 keys")

combined = []

for index, base_key in enumerate(base):
    # Preserve the original QWERTY tap/hold information.
    if isinstance(base_key, dict):
        result = base_key.copy()
    else:
        result = {"t": base_key}

    if index in LEFT:
        secondary_layers = LEFT_LAYERS
    elif index in RIGHT:
        secondary_layers = RIGHT_LAYERS
    else:
        # Leave thumb keys unchanged.
        combined.append(result)
        continue

    for layer_name, corner in secondary_layers:
        label = get_label(layers[layer_name][index])

        if label is not None:
            result[corner] = label

    combined.append(result)

# Preserve the layout, combos and other parser metadata.
output = data.copy()
output["layers"] = {"qwerty": combined}

# Keep the virtual combo layer if one was generated.
if "Combos" in layers:
    output["layers"]["Combos"] = layers["Combos"]

with open(destination, "w", encoding="utf-8") as f:
    yaml.safe_dump(
        output,
        f,
        sort_keys=False,
        allow_unicode=True,
    )

print(f"Generated combined overview YAML: {destination}")


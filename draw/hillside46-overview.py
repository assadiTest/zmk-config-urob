
#!/usr/bin/env python3

import sys
import re
import yaml


source, destination, keymap_file = sys.argv[1:4]


def read_key_positions(filename, macro_name):
    """Read a multiline KEYS_L / KEYS_R definition from a ZMK keymap."""

    with open(filename, encoding="utf-8") as f:
        lines = f.readlines()

    pattern = re.compile(
        rf"^\s*#define\s+{re.escape(macro_name)}\b(.*)$"
    )

    for i, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue

        content = match.group(1)

        # Collect continued macro lines.
        while lines[i].rstrip().endswith("\\"):
            content = content.rstrip()
            content = content[:-1]
            i += 1
            content += " " + lines[i].strip()

        # Extract physical key indices.
        positions = [int(x) for x in re.findall(r"\b\d+\b", content)]

        if len(positions) != len(set(positions)):
            raise ValueError(f"Duplicate positions in {macro_name}")

        return set(positions)

    raise ValueError(f"Could not find #define {macro_name} in {filename}")


def get_label(key):
    """Extract the tap/legend label from a parsed key."""
    if isinstance(key, dict):
        if key.get("type") in ("trans", "held"):
            return None
        return key.get("t")
    return key


# Read physical halves directly from the source keymap.
LEFT = read_key_positions(keymap_file, "KEYS_L")
RIGHT = read_key_positions(keymap_file, "KEYS_R")

if LEFT & RIGHT:
    raise ValueError("KEYS_L and KEYS_R contain overlapping positions")


# Layer-to-legend configuration.
LAYER_CONFIG = {
    "Symbol": {
        "side": "left",
        "corner": "tl",
        "color": "sym",
    },
    "Number": {
        "side": "left",
        "corner": "tr",
        "color": "num",
    },
    "Function": {
        "side": "left",
        "corner": "bl",
        "color": "fun",
    },
    "Navigation": {
        "side": "right",
        "corner": "tl",
        "color": "nav",
    },
    "Mouse": {
        "side": "right",
        "corner": "tr",
        "color": "mou",
    },
    "Media": {
        "side": "right",
        "corner": "bl",
        "color": "med",
    },
}

# Read generated keymap YAML.
with open(source, encoding="utf-8") as f:
    data = yaml.safe_load(f)

layers = data["layers"]
base = layers["qwerty"]

if len(base) != 46:
    raise ValueError(f"Expected 46 base keys, found {len(base)}")

for layer_name, _ in LEFT_LAYERS + RIGHT_LAYERS:
    if layer_name not in layers:
        raise ValueError(f"Missing layer: {layer_name}")

    if len(layers[layer_name]) != len(base):
        raise ValueError(
            f"Layer {layer_name} does not contain {len(base)} keys"
        )

for index in LEFT | RIGHT:
    if index >= len(base):
        raise ValueError(f"Invalid physical key position: {index}")


# Build combined overview.
combined = []

for index, base_key in enumerate(base):

    # Preserve original QWERTY tap/hold information.
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


# Preserve layout metadata and combos.
output = data.copy()
output["layers"] = {"qwerty": combined}

if "Combos" in layers:
    output["layers"]["Combos"] = layers["Combos"]


# Write overview YAML.
with open(destination, "w", encoding="utf-8") as f:
    yaml.safe_dump(
        output,
        f,
        sort_keys=False,
        allow_unicode=True,
    )

print(f"Generated combined overview YAML: {destination}")
print(f"Left-hand positions: {len(LEFT)}")
print(f"Right-hand positions: {len(RIGHT)}")


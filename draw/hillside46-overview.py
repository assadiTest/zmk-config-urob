
#!/usr/bin/env python3

import re
import sys
import yaml


source, destination, keymap_file, overview_config_file, css_destination = sys.argv[1:6]

with open(overview_config_file, encoding="utf-8") as f:
    overview_config = yaml.safe_load(f)


def read_key_positions(filename, macro_name):
    """Read a multiline physical-key-position macro."""

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

        while lines[i].rstrip().endswith("\\"):
            content = content.rstrip()[:-1]
            i += 1
            content += " " + lines[i].strip()

        positions = [int(x) for x in re.findall(r"\b\d+\b", content)]

        if len(positions) != len(set(positions)):
            raise ValueError(f"Duplicate positions in {macro_name}")

        return set(positions)

    raise ValueError(f"Could not find #define {macro_name}")


def get_label(key):
    """Extract the visible label from a parsed key."""

    if isinstance(key, dict):
        if key.get("type") in ("trans", "held"):
            return None
        return key.get("t")

    return key


def generate_css(base, layers, left, right, thumbs, layer_config):
    """Generate layer-specific colors using physical key positions."""

    rules = []

    # Color secondary legends on the alphanumeric keys.
    for index in sorted(left | right):
        side = "left" if index in left else "right"

        for layer_name, config in layer_config.items():
            if config["side"] != side:
                continue

            corner = config["corner"]
            color = config["color"]

            label = get_label(layers[layer_name][index])

            if label is None:
                continue

            rules.append(
                f".keypos-{index} text.{corner}, "
                f".keypos-{index} use.{corner} "
                f"{{ fill: var(--color-{color}); }}"
            )

    # Color secondary legends on thumb keys.
    for index in THUMBS:
        for layer_name, config in THUMB_LAYER_CONFIG.items():
            label = get_label(layers[layer_name][index])

            if label is None:
                continue

            corner = config["corner"]
            color = config["color"]

            rules.append(
                f".keypos-{index} text.{corner}, "
                f".keypos-{index} use.{corner} "
                f"{{ fill: var(--color-{color}); }}"
            )
    
    # Discover layer-toggle thumb keys from their hold labels.
    for index, key in enumerate(base):
        if not isinstance(key, dict):
            continue

        hold_label = key.get("h")

        for layer_name, config in layer_config.items():
            if hold_label == layer_name:
                color = config["color"]

                rules.append(
                    f".keypos-{index} text.hold "
                    f"{{ fill: var(--color-{color}); }}"
                )

        palette = """
svg.keymap {
    --color-sym: #b16286;
    --color-num: #d65d0e;
    --color-fun: #98971a;

    --color-nav: #d79921;
    --color-mou: #458588;
    --color-med: #689d6a;
}
"""

    return palette + "\n".join(rules) + "\n"


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

THUMB_LAYER_CONFIG = {
    "Navigation": {"corner": "tl", "color": "nav"},
    "Mouse":      {"corner": "tr", "color": "mou"},
    "Media":      {"corner": "bl", "color": "med"},
    "Symbol":     {"corner": "br", "color": "sym"},
    "Number":     {"corner": "br", "color": "num"},
    "Function":   {"corner": "br", "color": "fun"},
}


# Read physical key positions from the source keymap.
LEFT = read_key_positions(keymap_file, "KEYS_L")
RIGHT = read_key_positions(keymap_file, "KEYS_R")
THUMBS = read_key_positions(keymap_file, "KEYS_T")

if LEFT & RIGHT or LEFT & THUMBS or RIGHT & THUMBS:
    raise ValueError("Physical key position definitions overlap")


# Read generated keymap YAML.
with open(source, encoding="utf-8") as f:
    data = yaml.safe_load(f)

# Move all combos to the dedicated virtual layer.
for combo in data.get("combos", []):
    combo["layers"] = ["Combos"]

layers = data["layers"]
base = layers["qwerty"]

# Validate that physical position definitions cover the complete layout.
all_positions = LEFT | RIGHT | THUMBS
expected_positions = set(range(len(base)))

if all_positions != expected_positions:
    missing = sorted(expected_positions - all_positions)
    invalid = sorted(all_positions - expected_positions)

    raise ValueError(
        f"Physical key definitions do not match the parsed layout. "
        f"Missing: {missing}, invalid: {invalid}"
    )

# Validate configured layers.
for layer_name in LAYER_CONFIG:
    if layer_name not in layers:
        raise ValueError(f"Missing layer: {layer_name}")

    if len(layers[layer_name]) != len(base):
        raise ValueError(
            f"Layer {layer_name} does not contain {len(base)} keys"
        )


# Build combined overview.
combined = []

for index, base_key in enumerate(base):

    if isinstance(base_key, dict):
        result = base_key.copy()
    else:
        result = {"t": base_key}

    if index in THUMBS:
        for layer_name, config in THUMB_LAYER_CONFIG.items():
            label = get_label(layers[layer_name][index])

            if label is not None:
                result[config["corner"]] = label

        combined.append(result)
        continue

    if index in LEFT:
        side = "left"
    elif index in RIGHT:
        side = "right"
    else:
        combined.append(result)
        continue

    for layer_name, config in LAYER_CONFIG.items():
        if config["side"] != side:
            continue

        label = get_label(layers[layer_name][index])

        if label is not None:
            result[config["corner"]] = label

    combined.append(result)


# Preserve layout metadata and combos.
output = data.copy()
output["layers"] = {"qwerty": combined}

if "Combos" in layers:
    output["layers"]["Combos"] = layers["Combos"]


# Generate layer-specific CSS.
css = generate_css(base, layers, LEFT, RIGHT, THUMBS, LAYER_CONFIG)

# Add generated CSS as a drawing override.
draw_config = output.setdefault("draw_config", {})
existing_style = draw_config.get("svg_extra_style", "")

draw_config["svg_extra_style"] = (
    existing_style + "\n" + css
)


# Write overview YAML.
with open(destination, "w", encoding="utf-8") as f:
    yaml.safe_dump(
        output,
        f,
        sort_keys=False,
        allow_unicode=True,
    )


# Keep standalone CSS for easier debugging.
with open(css_destination, "w", encoding="utf-8") as f:
    f.write(css)


print(f"Generated combined overview YAML: {destination}")
print(f"Generated layer color CSS: {css_destination}")
print(f"Left-hand positions: {len(LEFT)}")
print(f"Right-hand positions: {len(RIGHT)}")
print(f"Thumb positions: {len(THUMBS)}")

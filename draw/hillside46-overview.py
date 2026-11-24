
#!/usr/bin/env python3

import re
import sys
import yaml
import copy
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen

if len(sys.argv) != 7:
    raise SystemExit(
        "Usage: hillside46-overview.py "
        "SOURCE DESTINATION KEYMAP OVERVIEW_CONFIG CSS_DESTINATION DRAW_CONFIG"
    )

(
    source,
    destination,
    keymap_file,
    overview_config_file,
    css_destination,
    draw_config_file,
) = sys.argv[1:7]


# ---------------------------------------------------------------------------
# Composite legend support
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

DEFAULT_GLYPH_URLS = {
    "mdi": "https://raw.githubusercontent.com/Templarian/MaterialDesign-SVG/master/svg/{}.svg",
    "mdil": "https://raw.githubusercontent.com/Pictogrammers/MaterialDesignLight/master/svg/{}.svg",
    "material": "https://fonts.gstatic.com/s/i/short-term/release/materialsymbolsoutlined/{}/default/48px.svg",
    "tabler": "https://unpkg.com/@tabler/icons/icons/outline/{}.svg",
    "phosphor": (
        "https://unpkg.com/@phosphor-icons/core@2.1.1/"
        "assets/{}.svg"
    ),
    "fa": "https://unpkg.com/@fortawesome/fontawesome-free/svgs/{}.svg",
}

LEGEND_FIELDS = (
    "t", "h", "s",
    "left", "right",
    "tl", "tr", "bl", "br",
)


def svg_tag(name):
    return f"{{{SVG_NS}}}{name}"


def load_glyph_urls(config_file):
    """Load configured glyph URLs, retaining keymap-drawer's defaults."""

    urls = DEFAULT_GLYPH_URLS.copy()

    if config_file:
        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        draw = config.get("draw_config", {})
        urls.update(draw.get("glyph_urls", {}))

    return urls


def fetch_source_glyph(source, name, urls, cache_dir):
    """Fetch and cache an original SVG glyph."""

    if source not in urls:
        raise ValueError(f"Unknown glyph source: {source}")

    glyph_id = name

    if source in ("phosphor", "fa"):
        if "/" not in name:
            raise ValueError(
                f"{source} glyphs require a variant/name, got: {name}"
            )

        variant, glyph_id = name.split("/", 1)
        glyph_id = f"{variant.lower()}/{glyph_id}"

        if source == "phosphor" and variant.lower() != "regular":
            glyph_id += f"-{variant.lower()}"

    url = urls[source].format(glyph_id)

    cache_key = hashlib.sha256(url.encode()).hexdigest()
    cache_file = cache_dir / f"{cache_key}.svg"

    if cache_file.exists():
        content = cache_file.read_text(encoding="utf-8")
    else:
        print(f"Fetching glyph: {source}:{name}")

        try:
            with urlopen(url, timeout=15) as response:
                content = response.read().decode("utf-8")
        except Exception as exc:
            raise RuntimeError(
                f"Unable to fetch glyph {source}:{name} from {url}"
            ) from exc

        cache_dir.mkdir(parents=True, exist_ok=True)

        temporary = cache_file.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(cache_file)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(
            f"Invalid SVG returned for {source}:{name}"
        ) from exc

    if root.tag.split("}")[-1] != "svg":
        raise ValueError(f"Glyph {source}:{name} is not an SVG")

    view_box = root.get("viewBox") or root.get("viewbox")

    if not view_box:
        raise ValueError(
            f"Glyph {source}:{name} has no viewBox"
        )

    try:
        x, y, width, height = map(float, view_box.replace(",", " ").split())
    except ValueError as exc:
        raise ValueError(
            f"Invalid viewBox in glyph {source}:{name}: {view_box}"
        ) from exc

    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid dimensions in glyph {source}:{name}")

    return root, (x, y, width, height)


def format_number(value):
    return f"{value:.4f}".rstrip("0").rstrip(".")



def estimate_text_width(value, size, letter_spacing=0):
    """Estimate rendered text width using approximate character metrics."""

    width = 0.0

    for char in value:
        if char.isspace():
            factor = 0.32
        elif char in "ilI.,:;!'|":
            factor = 0.28
        elif char in "mwMW@%&":
            factor = 0.85
        elif char.isupper():
            factor = 0.65
        elif char.isdigit():
            factor = 0.56
        else:
            factor = 0.53

        width += size * factor

    if len(value) > 1:
        width += (len(value) - 1) * letter_spacing

    return width


def compose_svg(definition, urls, cache_dir, behavior):
    """Compose glyphs and text with predictable alignment and scaling."""

    elements = definition.get("elements", [])
    layout = definition.get("layout", {})

    if not elements:
        raise ValueError(f"{behavior}: composite has no elements")

    direction = layout.get("direction", "horizontal")

    if direction not in ("horizontal", "vertical"):
        raise ValueError(f"{behavior}: invalid direction")

    # Main-axis and cross-axis alignment.
    # 'alignment' preserves compatibility with the original configuration.
    main_align = layout.get("main_align", "center")
    cross_align = layout.get(
        "cross_align",
        layout.get("alignment", "center"),
    )

    valid_alignments = ("start", "center", "end")

    if main_align not in valid_alignments:
        raise ValueError(f"{behavior}: invalid main_align")

    if cross_align not in valid_alignments:
        raise ValueError(f"{behavior}: invalid cross_align")

    gap = float(layout.get("gap", layout.get("spacing", 2)))
    padding = float(layout.get("padding", 1))

    if gap < 0 or padding < 0:
        raise ValueError(f"{behavior}: gap and padding cannot be negative")

    max_width = layout.get("max_width")
    max_height = layout.get("max_height")

    if max_width is not None:
        max_width = float(max_width)
        if max_width <= 0:
            raise ValueError(f"{behavior}: max_width must be positive")

    if max_height is not None:
        max_height = float(max_height)
        if max_height <= 0:
            raise ValueError(f"{behavior}: max_height must be positive")

    prepared = []

    for element in elements:
        kind = element.get("type")
        size = float(element.get("size", 16))

        if size <= 0:
            raise ValueError(f"{behavior}: element size must be positive")

        if kind == "glyph":
            source = element.get("source")
            name = element.get("name")

            if not source or not name:
                raise ValueError(
                    f"{behavior}: glyph needs source and name"
                )

            root, view_box = fetch_source_glyph(
                source, name, urls, cache_dir
            )

            vx, vy, vw, vh = view_box

            width = float(element.get("width", size * vw / vh))
            height = float(element.get("height", size))

            if width <= 0 or height <= 0:
                raise ValueError(
                    f"{behavior}: glyph dimensions must be positive"
                )

            prepared.append({
                "type": "glyph",
                "root": root,
                "view_box": view_box,
                "width": width,
                "height": height,
                "source": source,
            })

        elif kind == "text":
            value = str(element.get("value", ""))

            if not value:
                raise ValueError(f"{behavior}: empty text element")

            weight = element.get("weight", "normal")

            if weight not in (
                "normal", "bold", "400", "500", "600", "700"
            ):
                raise ValueError(
                    f"{behavior}: unsupported font weight {weight}"
                )

            letter_spacing = float(element.get("letter_spacing", 0))

            width = float(
                element.get(
                    "width",
                    estimate_text_width(
                        value, size, letter_spacing
                    ),
                )
            )

            height = float(element.get("height", size * 1.25))

            if width <= 0 or height <= 0:
                raise ValueError(
                    f"{behavior}: text dimensions must be positive"
                )

            prepared.append({
                "type": "text",
                "value": value,
                "width": width,
                "height": height,
                "size": size,
                "weight": weight,
                "letter_spacing": letter_spacing,
                "font_family": element.get(
                    "font_family", "inherit"
                ),
            })

        else:
            raise ValueError(
                f"{behavior}: unsupported element type {kind}"
            )

    # Calculate natural dimensions.
    if direction == "horizontal":
        content_width = (
            sum(item["width"] for item in prepared)
            + gap * (len(prepared) - 1)
        )
        content_height = max(item["height"] for item in prepared)
    else:
        content_width = max(item["width"] for item in prepared)
        content_height = (
            sum(item["height"] for item in prepared)
            + gap * (len(prepared) - 1)
        )

    natural_width = content_width + padding * 2
    natural_height = content_height + padding * 2

    # Uniform scaling prevents distortion and clipping.
    scale = 1.0

    if max_width is not None:
        scale = min(scale, max_width / natural_width)

    if max_height is not None:
        scale = min(scale, max_height / natural_height)

    scale *= float(layout.get("scale", 1))

    if scale <= 0:
        raise ValueError(f"{behavior}: scale must be positive")

    final_width = natural_width * scale
    final_height = natural_height * scale

    root = ET.Element(
        svg_tag("svg"),
        {
            "viewBox": (
                f"0 0 {format_number(final_width)} "
                f"{format_number(final_height)}"
            ),
            "color": "inherit",
        },
    )

    group = ET.SubElement(
        root,
        svg_tag("g"),
        {
            "transform": f"scale({format_number(scale)})",
            "color": "inherit",
        },
    )

    # Calculate each element's position.
    cursor = padding

    for item in prepared:

        if direction == "horizontal":
            available_cross = content_height

            if main_align == "start":
                x = cursor
            elif main_align == "end":
                x = padding + content_width - (
                    sum(i["width"] for i in prepared)
                    + gap * (len(prepared) - 1)
                ) + cursor - padding
            else:
                x = cursor

            if cross_align == "start":
                y = padding
            elif cross_align == "end":
                y = padding + available_cross - item["height"]
            else:
                y = padding + (
                    available_cross - item["height"]
                ) / 2

            cursor += item["width"] + gap

        else:
            if main_align == "start":
                y = cursor
            elif main_align == "end":
                y = padding + content_height - (
                    sum(i["height"] for i in prepared)
                    + gap * (len(prepared) - 1)
                ) + cursor - padding
            else:
                y = cursor

            if cross_align == "start":
                x = padding
            elif cross_align == "end":
                x = padding + content_width - item["width"]
            else:
                x = padding + (
                    content_width - item["width"]
                ) / 2

            cursor += item["height"] + gap

        if item["type"] == "glyph":
            vx, vy, vw, vh = item["view_box"]

            nested = ET.SubElement(
                group,
                svg_tag("svg"),
                {
                    "x": format_number(x),
                    "y": format_number(y),
                    "width": format_number(item["width"]),
                    "height": format_number(item["height"]),
                    "viewBox": " ".join(
                        format_number(v)
                        for v in (vx, vy, vw, vh)
                    ),
                    "preserveAspectRatio": "xMidYMid meet",
                    "color": "inherit",
                },
            )

            if item["source"] == "tabler":
                nested.set("class", "icon-tabler")

            for child in item["root"]:
                nested.append(copy.deepcopy(child))

        else:
            text = ET.SubElement(
                group,
                svg_tag("text"),
                {
                    "x": format_number(x + item["width"] / 2),
                    "y": format_number(y + item["height"] / 2),
                    "fill": "inherit",
                    "text-anchor": "middle",
                    "dominant-baseline": "middle",
                    "font-size": format_number(item["size"]),
                    "font-weight": str(item["weight"]),
                    "font-family": item["font_family"],
                    "letter-spacing": format_number(
                        item["letter_spacing"]
                    ),
                    "textLength": format_number(item["width"]),
                    "lengthAdjust": "spacingAndGlyphs",
                },
            )

            text.text = item["value"]

    return ET.tostring(root, encoding="unicode")


def register_composite_glyphs(data, overview_config, draw_config_file):
    """Generate composite glyphs and replace their markers in the keymap."""

    definitions = overview_config.get("composite_legends", {})

    if not definitions:
        return

    urls = load_glyph_urls(draw_config_file)

    marker_to_glyph = {}
    generated_glyphs = {}

    for behavior, definition in definitions.items():
        marker = definition.get("marker")

        if not marker:
            raise ValueError(
                f"{behavior}: composite definition has no marker"
            )

        if marker in marker_to_glyph:
            raise ValueError(f"Duplicate composite marker: {marker}")

        slug = re.sub(r"[^a-zA-Z0-9]+", "_", behavior).strip("_")
        digest = hashlib.sha1(behavior.encode()).hexdigest()[:8]
        glyph_name = f"composite_{slug}_{digest}"

        if not re.fullmatch(r"[a-zA-Z0-9_]+", glyph_name):
            raise ValueError(f"Invalid generated glyph name: {glyph_name}")

        svg = compose_svg(
            definition,
            urls,
            cache_dir,
            behavior,
        )

        marker_to_glyph[marker] = f"$${glyph_name}$$"
        generated_glyphs[glyph_name] = svg

    found_markers = set()

    def replace_value(value):
        if isinstance(value, str):
            if value in marker_to_glyph:
                found_markers.add(value)
                return marker_to_glyph[value]

            if value.startswith("__COMPOSITE_"):
                raise ValueError(
                    f"No composite_legends definition for marker: {value}"
                )

        if isinstance(value, dict):
            return {
                key: replace_value(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [replace_value(item) for item in value]

        return value

    data["layers"] = replace_value(data["layers"])

    if "combos" in data:
        data["combos"] = replace_value(data["combos"])

    unused = set(marker_to_glyph) - found_markers

    if unused:
        print(
            "Warning: unused composite definitions:",
            ", ".join(sorted(unused)),
        )

    draw_config = data.setdefault("draw_config", {})
    glyphs = draw_config.setdefault("glyphs", {})

    for name, svg in generated_glyphs.items():
        if name in glyphs:
            raise ValueError(
                f"Composite glyph conflicts with existing glyph: {name}"
            )

        glyphs[name] = svg

    print(f"Generated composite glyphs: {len(generated_glyphs)}")
    print(f"Matched composite markers: {len(found_markers)}")


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


def generate_css(
    base,
    layers,
    left,
    right,
    thumbs,
    layer_config,
    thumb_layer_config,
):
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

        def generate_palette(colors):
            return (
                "svg.keymap {\n"
                + "\n".join(
                    f"    --color-{name}: {value};"
                    for name, value in colors.items()
                )
                + "\n}\n"
            )

    return palette + "\n".join(rules) + "\n"


# Layer-to-legend configuration.
def build_layer_config(config):
    result = {}

    for side in ("left", "right"):
        for legend in config["legends"].get(side, []):
            layer = legend["layer"]

            if layer in result:
                raise ValueError(
                    f"Layer configured more than once: {layer}"
                )

            corner = legend["corner"]
            if corner not in ("tl", "tr", "bl", "br"):
                raise ValueError(
                    f"Invalid corner for {layer}: {corner}"
                )

            result[layer] = {
                "side": side,
                "corner": corner,
                "color": legend["color"],
            }

    return result


def build_thumb_config(config):
    return {
        item["layer"]: {
            "corner": item["corner"],
            "color": item["color"],
        }
        for item in config["legends"].get("thumbs", [])
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

# Generate composite SVG glyphs and replace their markers.
def register_composite_glyphs(
    data,
    overview_config,
    draw_config_file,
    cache_dir,
):

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

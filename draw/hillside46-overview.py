
#!/usr/bin/env python3
"""
Generic keymap overview generator for keymap-drawer.

Generates an intermediate YAML file containing a combined keyboard
overview. The final SVG is rendered by keymap-drawer.

Keyboard-specific settings belong in the overview YAML configuration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET

from pathlib import Path
from urllib.request import urlopen

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"

ET.register_namespace("", SVG_NS)

SUPPORTED_PHOSPHOR_WEIGHTS = {
    "thin",
    "light",
    "regular",
    "bold",
    "fill",
    "duotone",
}

VALID_CORNERS = {"tl", "tr", "bl", "br"}

VALID_ALIGNMENTS = {"start", "center", "end"}

DEFAULT_GLYPH_URLS = {
    "mdi": (
        "https://raw.githubusercontent.com/"
        "Templarian/MaterialDesign-SVG/master/svg/{}.svg"
    ),
    "mdil": (
        "https://raw.githubusercontent.com/"
        "Pictogrammers/MaterialDesignLight/master/svg/{}.svg"
    ),
    "material": (
        "https://fonts.gstatic.com/s/i/short-term/release/"
        "materialsymbolsoutlined/{}/default/48px.svg"
    ),
    "tabler": (
        "https://unpkg.com/@tabler/icons/icons/outline/{}.svg"
    ),
    "phosphor": (
        "https://unpkg.com/@phosphor-icons/core@2.1.1/"
        "assets/{}.svg"
    ),
    "fa": (
        "https://unpkg.com/@fortawesome/fontawesome-free/"
        "svgs/{}.svg"
    ),
}


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def svg_tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def format_number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def load_yaml(path: Path) -> dict:
    """Load YAML and ensure its root is a mapping."""

    with path.open(encoding="utf-8") as file:
        result = yaml.safe_load(file)

    if not isinstance(result, dict):
        raise ValueError(f"{path}: expected a YAML mapping")

    return result


def write_text_atomic(path: Path, content: str) -> None:
    """Write a text file atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.tmp"
    )

    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Glyph source handling
# ---------------------------------------------------------------------------

def load_glyph_urls(config_file: Path | None) -> dict:
    """Load glyph URL templates, retaining built-in providers."""

    urls = DEFAULT_GLYPH_URLS.copy()

    if config_file is None:
        return urls

    config = load_yaml(config_file)

    draw_config = config.get("draw_config", {})
    custom_urls = draw_config.get("glyph_urls", {})

    if not isinstance(custom_urls, dict):
        raise ValueError(
            "draw_config.glyph_urls must be a mapping"
        )

    urls.update(custom_urls)

    return urls


def resolve_glyph_id(source: str, name: str) -> str:
    """Convert a provider/name pair into its asset path."""

    if source == "phosphor":
        if "/" not in name:
            raise ValueError(
                "Phosphor glyphs require weight/name, "
                f"got: {name}"
            )

        weight, icon_name = name.split("/", 1)

        if weight not in SUPPORTED_PHOSPHOR_WEIGHTS:
            raise ValueError(
                f"Unsupported Phosphor weight: {weight}"
            )

        if (
            not icon_name
            or "/" in icon_name
            or "\\" in icon_name
            or ".." in icon_name
        ):
            raise ValueError(
                f"Invalid Phosphor icon name: {icon_name}"
            )

        return f"{weight}/{icon_name}"

    if source == "fa":
        if "/" not in name:
            raise ValueError(
                "Font Awesome glyphs require variant/name"
            )

        variant, icon_name = name.split("/", 1)

        if not variant or not icon_name or ".." in icon_name:
            raise ValueError(
                f"Invalid Font Awesome glyph name: {name}"
            )

        return f"{variant}/{icon_name}"

    return name


def validate_svg(content: str, source: str, name: str):
    """Validate an SVG and return its root and viewBox."""

    try:
        root = ET.fromstring(content)

    except ET.ParseError as exc:
        raise ValueError(
            f"Invalid SVG returned for {source}:{name}"
        ) from exc

    if root.tag.split("}")[-1] != "svg":
        raise ValueError(
            f"Glyph {source}:{name} is not an SVG"
        )

    view_box = root.get("viewBox") or root.get("viewbox")

    if not view_box:
        raise ValueError(
            f"Glyph {source}:{name} has no viewBox"
        )

    try:
        x, y, width, height = map(
            float,
            view_box.replace(",", " ").split(),
        )

    except ValueError as exc:
        raise ValueError(
            f"Invalid viewBox for {source}:{name}: {view_box}"
        ) from exc

    if width <= 0 or height <= 0:
        raise ValueError(
            f"Invalid dimensions for {source}:{name}"
        )

    return root, (x, y, width, height)


def fetch_source_glyph(
    source: str,
    name: str,
    urls: dict,
    cache_dir: Path,
):
    """Fetch, validate and cache an original SVG glyph."""

    if source not in urls:
        raise ValueError(f"Unknown glyph source: {source}")

    glyph_id = resolve_glyph_id(source, name)
    url = urls[source].format(glyph_id)

    cache_key = hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()

    cache_file = cache_dir / f"{cache_key}.svg"

    if cache_file.exists():
        content = cache_file.read_text(encoding="utf-8")

    else:
        print(f"Fetching glyph: {source}:{name}")

        try:
            with urlopen(url, timeout=20) as response:
                content = response.read().decode("utf-8")

        except Exception as exc:
            raise RuntimeError(
                f"Unable to fetch glyph {source}:{name}\n"
                f"URL: {url}"
            ) from exc

        validate_svg(content, source, name)

        cache_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(cache_file, content)

    root, view_box = validate_svg(
        content,
        source,
        name,
    )

    return root, view_box


# ---------------------------------------------------------------------------
# Generic glyph discovery and resolution
# ---------------------------------------------------------------------------

GLYPH_PATTERN = re.compile(r"\$\$([^$]+)\$\$")


def find_glyph_references(value) -> set[str]:
    """Recursively find all $$glyph$$ references in nested data."""

    references = set()

    if isinstance(value, str):
        references.update(GLYPH_PATTERN.findall(value))

    elif isinstance(value, dict):
        for item in value.values():
            references.update(find_glyph_references(item))

    elif isinstance(value, list):
        for item in value:
            references.update(find_glyph_references(item))

    return references


def serialize_glyph(root: ET.Element) -> str:
    """Serialize an SVG root into an embedded glyph definition."""

    return ET.tostring(root, encoding="unicode")


def resolve_missing_glyphs(
    data: dict,
    urls: dict,
    cache_dir: Path,
) -> None:
    """
    Resolve all missing provider glyphs and embed their SVG definitions.

    Existing custom glyphs and generated composite glyphs are preserved.
    Provider URLs are used only during generation, never during rendering.
    """

    draw_config = data.setdefault("draw_config", {})

    glyphs = draw_config.setdefault("glyphs", {})

    if not isinstance(glyphs, dict):
        raise ValueError(
            "draw_config.glyphs must be a mapping"
        )

    references = find_glyph_references(data)

    missing = references - set(glyphs)

    resolved = {}

    for reference in sorted(missing):
        source, separator, name = reference.partition(":")

        if not separator or not source or not name:
            raise ValueError(
                f"Glyph reference {reference!r} is not defined "
                "in draw_config.glyphs and has no provider"
            )

        if source not in urls:
            raise ValueError(
                f"No glyph provider URL configured for "
                f"{reference!r}"
            )

        root, _ = fetch_source_glyph(
            source,
            name,
            urls,
            cache_dir,
        )

        resolved[reference] = serialize_glyph(root)

    # Register all resolved glyphs.
    glyphs.update(resolved)

    # Verify that every reference is now available.
    unresolved = references - set(glyphs)

    if unresolved:
        raise ValueError(
            "Unresolved glyph references: "
            + ", ".join(sorted(unresolved))
        )

    # Remote fetching must not be necessary during final rendering.
    draw_config.pop("glyph_urls", None)

    print(
        f"Glyph references found: {len(references)}"
    )

    print(
        f"Existing glyph definitions: "
        f"{len(references) - len(resolved)}"
    )

    print(
        f"Downloaded glyph definitions: {len(resolved)}"
    )

    print(
        f"Total embedded glyph definitions: {len(glyphs)}"
    )

# ---------------------------------------------------------------------------
# Composite SVG generation
# ---------------------------------------------------------------------------

def estimate_text_width(
    value: str,
    size: float,
    letter_spacing: float = 0,
) -> float:
    """Estimate text width using approximate character metrics."""

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


def compose_svg(
    definition: dict,
    urls: dict,
    cache_dir: Path,
    behavior: str,
) -> str:
    """Compose glyphs and text into a single SVG glyph."""

    elements = definition.get("elements", [])
    layout = definition.get("layout", {})

    if not elements:
        raise ValueError(
            f"{behavior}: composite has no elements"
        )

    direction = layout.get("direction", "horizontal")

    if direction not in ("horizontal", "vertical"):
        raise ValueError(
            f"{behavior}: invalid direction"
        )

    main_align = layout.get("main_align", "center")
    cross_align = layout.get(
        "cross_align",
        layout.get("alignment", "center"),
    )

    if main_align not in VALID_ALIGNMENTS:
        raise ValueError(
            f"{behavior}: invalid main_align"
        )

    if cross_align not in VALID_ALIGNMENTS:
        raise ValueError(
            f"{behavior}: invalid cross_align"
        )

    gap = float(layout.get("gap", layout.get("spacing", 2)))
    padding = float(layout.get("padding", 1))

    if gap < 0 or padding < 0:
        raise ValueError(
            f"{behavior}: gap and padding cannot be negative"
        )

    max_width = layout.get("max_width")
    max_height = layout.get("max_height")

    if max_width is not None:
        max_width = float(max_width)

        if max_width <= 0:
            raise ValueError(
                f"{behavior}: max_width must be positive"
            )

    if max_height is not None:
        max_height = float(max_height)

        if max_height <= 0:
            raise ValueError(
                f"{behavior}: max_height must be positive"
            )

    prepared = []

    for element in elements:
        kind = element.get("type")
        size = float(element.get("size", 16))

        if size <= 0:
            raise ValueError(
                f"{behavior}: element size must be positive"
            )

        if kind == "glyph":
            source = element.get("source")
            name = element.get("name")

            if not source or not name:
                raise ValueError(
                    f"{behavior}: glyph needs source and name"
                )

            root, view_box = fetch_source_glyph(
                source,
                name,
                urls,
                cache_dir,
            )

            vx, vy, vw, vh = view_box

            width = float(
                element.get("width", size * vw / vh)
            )
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
                raise ValueError(
                    f"{behavior}: empty text element"
                )

            weight = element.get("weight", "normal")

            if weight not in (
                "normal", "bold", "400",
                "500", "600", "700",
            ):
                raise ValueError(
                    f"{behavior}: unsupported font weight {weight}"
                )

            letter_spacing = float(
                element.get("letter_spacing", 0)
            )

            width = float(
                element.get(
                    "width",
                    estimate_text_width(
                        value,
                        size,
                        letter_spacing,
                    ),
                )
            )

            height = float(
                element.get("height", size * 1.25)
            )

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
                    "font_family",
                    "inherit",
                ),
            })

        else:
            raise ValueError(
                f"{behavior}: unsupported element type {kind}"
            )

    if direction == "horizontal":
        content_width = (
            sum(item["width"] for item in prepared)
            + gap * (len(prepared) - 1)
        )
        content_height = max(
            item["height"] for item in prepared
        )

    else:
        content_width = max(
            item["width"] for item in prepared
        )
        content_height = (
            sum(item["height"] for item in prepared)
            + gap * (len(prepared) - 1)
        )

    natural_width = content_width + padding * 2
    natural_height = content_height + padding * 2

    scale = 1.0

    if max_width is not None:
        scale = min(scale, max_width / natural_width)

    if max_height is not None:
        scale = min(scale, max_height / natural_height)

    scale *= float(layout.get("scale", 1))

    if scale <= 0:
        raise ValueError(
            f"{behavior}: scale must be positive"
        )

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

    cursor = padding

    for item in prepared:
        if direction == "horizontal":
            x = cursor

            if cross_align == "start":
                y = padding
            elif cross_align == "end":
                y = padding + content_height - item["height"]
            else:
                y = padding + (
                    content_height - item["height"]
                ) / 2

            cursor += item["width"] + gap

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

            for child in item["root"]:
                nested.append(copy.deepcopy(child))

        else:
            text = ET.SubElement(
                group,
                svg_tag("text"),
                {
                    "x": format_number(
                        x + item["width"] / 2
                    ),
                    "y": format_number(
                        y + item["height"] / 2
                    ),
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


# ---------------------------------------------------------------------------
# Composite marker registration
# ---------------------------------------------------------------------------


def register_composite_glyphs(
    data: dict,
    overview_config: dict,
    draw_config_file: Path,
    cache_dir: Path,
) -> None:
    """
    Generate composite glyphs, replace their markers and merge
    the original drawing configuration into the parsed keymap.
    """

    urls = load_glyph_urls(draw_config_file)

    # Load the original drawing configuration.
    source_config = load_yaml(draw_config_file)

    source_draw_config = source_config.get(
        "draw_config",
        {},
    )

    if not isinstance(source_draw_config, dict):
        raise ValueError(
            "draw_config in the original configuration "
            "must be a mapping"
        )

    # Preserve existing parsed drawing configuration.
    draw_config = data.setdefault("draw_config", {})

    if not isinstance(draw_config, dict):
        raise ValueError(
            "Parsed keymap draw_config must be a mapping"
        )

    # Merge original drawing configuration without overwriting
    # settings already present in the parsed keymap.
    for key, value in source_draw_config.items():
        if key in ("glyph_urls", "glyphs"):
            continue

        draw_config.setdefault(
            key,
            copy.deepcopy(value),
        )

    # Merge original custom glyphs with existing glyphs.
    source_glyphs = source_draw_config.get("glyphs", {})
    existing_glyphs = draw_config.get("glyphs", {})

    if not isinstance(source_glyphs, dict):
        raise ValueError(
            "draw_config.glyphs in original configuration "
            "must be a mapping"
        )

    if not isinstance(existing_glyphs, dict):
        raise ValueError(
            "Parsed draw_config.glyphs must be a mapping"
        )

    merged_glyphs = copy.deepcopy(source_glyphs)

    # Parsed keymap definitions take precedence.
    merged_glyphs.update(existing_glyphs)

    draw_config["glyphs"] = merged_glyphs

    # Composite generation.
    definitions = overview_config.get(
        "composite_legends",
        {},
    )

    if not isinstance(definitions, dict):
        raise ValueError(
            "composite_legends must be a mapping"
        )

    marker_to_glyph = {}
    generated_glyphs = {}

    for behavior, definition in definitions.items():

        marker = definition.get("marker")

        if not marker:
            raise ValueError(
                f"{behavior}: composite definition has no marker"
            )

        if marker in marker_to_glyph:
            raise ValueError(
                f"Duplicate composite marker: {marker}"
            )

        slug = re.sub(
            r"[^a-zA-Z0-9]+",
            "_",
            behavior,
        ).strip("_")

        digest = hashlib.sha1(
            behavior.encode("utf-8")
        ).hexdigest()[:8]

        glyph_name = f"composite_{slug}_{digest}"

        svg = compose_svg(
            definition,
            urls,
            cache_dir,
            behavior,
        )

        # Validate generated SVG before registration.
        root, _ = validate_svg(
            svg,
            "composite",
            glyph_name,
        )

        marker_to_glyph[marker] = f"$${glyph_name}$$"

        generated_glyphs[glyph_name] = serialize_glyph(root)

    # Replace composite markers recursively.
    found_markers = set()

    def replace_value(value):

        if isinstance(value, str):

            if value in marker_to_glyph:
                found_markers.add(value)
                return marker_to_glyph[value]

            if value.startswith("__COMPOSITE_"):
                raise ValueError(
                    "No composite_legends definition for marker: "
                    f"{value}"
                )

            return value

        if isinstance(value, dict):
            return {
                key: replace_value(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                replace_value(item)
                for item in value
            ]

        return value

    replaced = replace_value(data)

    data.clear()
    data.update(replaced)

    unused = set(marker_to_glyph) - found_markers

    if unused:
        print(
            "Warning: unused composite definitions: "
            + ", ".join(sorted(unused))
        )

    # Register generated composite SVGs.
    glyphs = data["draw_config"]["glyphs"]

    for name, svg in generated_glyphs.items():

        if name in glyphs:
            raise ValueError(
                "Composite glyph conflicts with existing "
                f"glyph definition: {name}"
            )

        glyphs[name] = svg

    print(
        f"Generated composite glyphs: {len(generated_glyphs)}"
    )

    print(
        f"Matched composite markers: {len(found_markers)}"
    )

# ---------------------------------------------------------------------------
# Physical key positions
# ---------------------------------------------------------------------------

def read_key_positions(
    filename: Path,
    macro_name: str,
) -> set[int]:
    """Read a multiline physical-key-position macro."""

    lines = filename.read_text(
        encoding="utf-8"
    ).splitlines()

    pattern = re.compile(
        rf"^\s*#define\s+{re.escape(macro_name)}\b(.*)$"
    )

    for index, line in enumerate(lines):
        match = pattern.match(line)

        if not match:
            continue

        content = match.group(1)

        while lines[index].rstrip().endswith("\\"):
            content = content.rstrip()[:-1]
            index += 1

            if index >= len(lines):
                raise ValueError(
                    f"Unterminated macro: {macro_name}"
                )

            content += " " + lines[index].strip()

        positions = [
            int(value)
            for value in re.findall(r"\b\d+\b", content)
        ]

        if not positions:
            raise ValueError(
                f"Macro {macro_name} contains no positions"
            )

        if len(positions) != len(set(positions)):
            raise ValueError(
                f"Duplicate positions in {macro_name}"
            )

        return set(positions)

    raise ValueError(
        f"Could not find #define {macro_name}"
    )


# ---------------------------------------------------------------------------
# Overview configuration validation
# ---------------------------------------------------------------------------

def validate_corner(value: str, context: str) -> None:
    if value not in VALID_CORNERS:
        raise ValueError(
            f"{context}: invalid corner {value!r}; "
            f"expected one of {sorted(VALID_CORNERS)}"
        )


def validate_overview_config(config: dict) -> None:
    """Validate the generic overview configuration."""

    if config.get("version") != 1:
        raise ValueError(
            "Unsupported overview configuration version"
        )

    for key in ("base_layer", "hands", "legends"):
        if key not in config:
            raise ValueError(
                f"Overview configuration is missing {key!r}"
            )

    if not isinstance(config["base_layer"], str):
        raise ValueError("base_layer must be a string")

    for hand in ("left", "right", "thumbs"):
        macro = config["hands"].get(hand)

        if not isinstance(macro, str) or not macro.strip():
            raise ValueError(
                f"hands.{hand} must contain a macro name"
            )

    for side in ("left", "right", "thumbs"):
        items = config["legends"].get(side, [])

        if not isinstance(items, list):
            raise ValueError(
                f"legends.{side} must be a list"
            )

        for item in items:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Invalid legend definition in {side}"
                )

            for key in ("layer", "corner", "color"):
                if key not in item:
                    raise ValueError(
                        f"Legend in {side} is missing {key!r}"
                    )

            if not isinstance(item["layer"], str):
                raise ValueError(
                    f"Legend layer in {side} must be a string"
                )

            validate_corner(
                item["corner"],
                f"legends.{side}.{item['layer']}",
            )

            color = item["color"]

            if not isinstance(color, str) or not re.fullmatch(
                r"[a-zA-Z][a-zA-Z0-9_-]*",
                color,
            ):
                raise ValueError(
                    f"Invalid color variable: {color!r}"
                )

    definitions = config.get("composite_legends", {})

    if not isinstance(definitions, dict):
        raise ValueError(
            "composite_legends must be a mapping"
        )


def build_layer_config(config: dict) -> dict:
    """Build left/right layer configuration from YAML."""

    result = {}

    for side in ("left", "right"):
        for legend in config["legends"].get(side, []):
            layer = legend["layer"]

            if layer in result:
                raise ValueError(
                    f"Layer configured more than once: {layer}"
                )

            result[layer] = {
                "side": side,
                "corner": legend["corner"],
                "color": legend["color"],
            }

    return result


def build_thumb_config(config: dict) -> dict:
    """Build thumb-layer configuration from YAML."""

    result = {}

    for legend in config["legends"].get("thumbs", []):
        layer = legend["layer"]

        if layer in result:
            raise ValueError(
                f"Thumb layer configured more than once: {layer}"
            )

        result[layer] = {
            "corner": legend["corner"],
            "color": legend["color"],
        }

    return result


# ---------------------------------------------------------------------------
# Layer helpers
# ---------------------------------------------------------------------------

def get_label(key):
    """Extract the visible label from a parsed key."""

    if isinstance(key, dict):
        if key.get("type") in ("trans", "held"):
            return None

        return key.get("t")

    return key


def validate_layers(
    layers: dict,
    base_layer: str,
    layer_config: dict,
    thumb_config: dict,
):
    """Validate configured layers against the parsed keymap."""

    if base_layer not in layers:
        raise ValueError(
            f"Base layer not found: {base_layer}"
        )

    base = layers[base_layer]

    configured_layers = (
        set(layer_config) | set(thumb_config)
    )

    for layer_name in configured_layers:
        if layer_name not in layers:
            raise ValueError(
                f"Configured layer not found: {layer_name}"
            )

        if len(layers[layer_name]) != len(base):
            raise ValueError(
                f"Layer {layer_name} has "
                f"{len(layers[layer_name])} keys; "
                f"expected {len(base)}"
            )

    return base


# ---------------------------------------------------------------------------
# CSS generation
# ---------------------------------------------------------------------------

def generate_css(
    base: list,
    layers: dict,
    left: set[int],
    right: set[int],
    thumbs: set[int],
    layer_config: dict,
    thumb_config: dict,
) -> str:
    """Generate layer-specific CSS using physical key positions."""

    rules = []

    # Color secondary legends on alphanumeric keys.
    for index in sorted(left | right):
        side = "left" if index in left else "right"

        for layer_name, config in layer_config.items():
            if config["side"] != side:
                continue

            label = get_label(
                layers[layer_name][index]
            )

            if label is None:
                continue

            corner = config["corner"]
            color = config["color"]

            rules.append(
                f".keypos-{index} text.{corner}, "
                f".keypos-{index} use.{corner} "
                f"{{ fill: var(--color-{color}); }}"
            )

    # Color secondary legends on thumb keys.
    for index in sorted(thumbs):
        for layer_name, config in thumb_config.items():
            label = get_label(
                layers[layer_name][index]
            )

            if label is None:
                continue

            corner = config["corner"]
            color = config["color"]

            rules.append(
                f".keypos-{index} text.{corner}, "
                f".keypos-{index} use.{corner} "
                f"{{ fill: var(--color-{color}); }}"
            )

    # Color layer-toggle hold labels.
    for index, key in enumerate(base):
        if not isinstance(key, dict):
            continue

        hold_label = key.get("h")

        for layer_name, config in layer_config.items():
            if hold_label == layer_name:
                rules.append(
                    f".keypos-{index} text.hold "
                    f"{{ fill: var(--color-{config['color']}); }}"
                )

    return "\n".join(rules) + "\n"


# ---------------------------------------------------------------------------
# Overview generation
# ---------------------------------------------------------------------------

def generate_overview(
    data: dict,
    config: dict,
    keymap_file: Path,
):
    """Generate a combined keymap overview."""

    validate_overview_config(config)

    layers = data.get("layers")

    if not isinstance(layers, dict):
        raise ValueError(
            "Parsed keymap has no layers mapping"
        )

    base_layer = config["base_layer"]

    layer_config = build_layer_config(config)
    thumb_config = build_thumb_config(config)

    base = validate_layers(
        layers,
        base_layer,
        layer_config,
        thumb_config,
    )

    # Read physical positions from configured macros.
    positions = {
        name: read_key_positions(
            keymap_file,
            macro,
        )
        for name, macro in config["hands"].items()
    }

    left = positions["left"]
    right = positions["right"]
    thumbs = positions["thumbs"]

    # Physical groups must not overlap.
    if (
        left & right
        or left & thumbs
        or right & thumbs
    ):
        raise ValueError(
            "Physical key position definitions overlap"
        )

    all_positions = left | right | thumbs
    expected_positions = set(range(len(base)))

    if all_positions != expected_positions:
        missing = sorted(
            expected_positions - all_positions
        )

        invalid = sorted(
            all_positions - expected_positions
        )

        raise ValueError(
            "Physical key definitions do not match the parsed "
            f"layout. Missing: {missing}, invalid: {invalid}"
        )

    # Build combined overview.
    combined = []

    for index, base_key in enumerate(base):

        if isinstance(base_key, dict):
            result = copy.deepcopy(base_key)

        else:
            result = {"t": base_key}

        # Thumb keys.
        if index in thumbs:
            for layer_name, item in thumb_config.items():
                label = get_label(
                    layers[layer_name][index]
                )

                if label is not None:
                    result[item["corner"]] = label

            combined.append(result)
            continue

        # Alphanumeric keys.
        if index in left:
            side = "left"

        elif index in right:
            side = "right"

        else:
            combined.append(result)
            continue

        for layer_name, item in layer_config.items():
            if item["side"] != side:
                continue

            label = get_label(
                layers[layer_name][index]
            )

            if label is not None:
                result[item["corner"]] = label

        combined.append(result)

    # Preserve parsed metadata and drawing configuration.
    output = copy.deepcopy(data)

    output["layers"] = {
        base_layer: combined,
    }

    # Preserve the dedicated virtual combo layer.
    if "Combos" in layers:
        output["layers"]["Combos"] = layers["Combos"]

    # Move combos to their dedicated virtual layer.
    for combo in output.get("combos", []):
        combo["layers"] = ["Combos"]

    # Generate layer-specific CSS.
    css = generate_css(
        base,
        layers,
        left,
        right,
        thumbs,
        layer_config,
        thumb_config,
    )

    # Add CSS as a drawing override.
    draw_config = output.setdefault(
        "draw_config",
        {},
    )

    existing_style = draw_config.get(
        "svg_extra_style",
        "",
    )

    draw_config["svg_extra_style"] = (
        existing_style.rstrip()
        + "\n"
        + css
    )

    return output, css, positions


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def parse_arguments():
    """Parse both named and legacy positional arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate a combined keymap overview "
            "for keymap-drawer."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Legacy positional interface:
    # SOURCE DESTINATION KEYMAP CONFIG CSS_DESTINATION DRAW_CONFIG
    parser.add_argument(
        "legacy",
        nargs="*",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--source",
        type=Path,
        help="Parsed keymap YAML",
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Generated overview YAML",
    )

    parser.add_argument(
        "--keymap",
        type=Path,
        help="Original ZMK keymap source",
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="Keyboard-specific overview configuration",
    )

    parser.add_argument(
        "--draw-config",
        type=Path,
        help="General keymap-drawer configuration",
    )

    parser.add_argument(
        "--css-output",
        type=Path,
        help="Optional standalone generated CSS",
    )

    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Glyph cache directory",
    )

    args = parser.parse_args()

    named_values = (
        args.source,
        args.output,
        args.keymap,
        args.config,
        args.draw_config,
    )

    if args.legacy:
        if len(args.legacy) != 6:
            parser.error(
                "Legacy invocation requires exactly six arguments"
            )

        if any(value is not None for value in named_values):
            parser.error(
                "Do not mix positional and named arguments"
            )

        (
            args.source,
            args.output,
            args.keymap,
            args.config,
            legacy_css,
            args.draw_config,
        ) = map(Path, args.legacy)

        if args.css_output is not None:
            parser.error(
                "CSS output specified more than once"
            )

        args.css_output = legacy_css

    else:
        required = {
            "--source": args.source,
            "--output": args.output,
            "--keymap": args.keymap,
            "--config": args.config,
            "--draw-config": args.draw_config,
        }

        missing = [
            name
            for name, value in required.items()
            if value is None
        ]

        if missing:
            parser.error(
                "Missing required arguments: "
                + ", ".join(missing)
            )

    if args.css_output is None:
        args.css_output = args.output.with_suffix(".css")

    if args.cache_dir is None:
        args.cache_dir = (
            args.output.parent / ".glyph-cache"
        )

    return args


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_arguments()

    try:
        # Load inputs.
        overview_config = load_yaml(args.config)
        data = load_yaml(args.source)

        # Generate and register composite glyphs.
        register_composite_glyphs(
            data,
            overview_config,
            args.draw_config,
            args.cache_dir,
        )

        # Generate combined overview.
        output, css, positions = generate_overview(
            data,
            overview_config,
            args.keymap,
        )
        
        # Resolve every glyph referenced by the final overview.
        # This must happen after composite registration and layer merging.
        glyph_urls = load_glyph_urls(args.draw_config)
        
        resolve_missing_glyphs(
            output,
            glyph_urls,
            args.cache_dir,
        )

        # Write intermediate YAML.
        yaml_content = yaml.safe_dump(
            output,
            sort_keys=False,
            allow_unicode=True,
        )

        write_text_atomic(
            args.output,
            yaml_content,
        )

        # Write standalone CSS for inspection.
        write_text_atomic(
            args.css_output,
            css,
        )

        print(
            f"Generated overview YAML: {args.output}"
        )

        print(
            f"Generated layer CSS: {args.css_output}"
        )

        for name, values in positions.items():
            print(
                f"{name.capitalize()} positions: {len(values)}"
            )

        return 0

    except (
        OSError,
        ValueError,
        RuntimeError,
        yaml.YAMLError,
    ) as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())


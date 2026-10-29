"""
Anchor-Relative Field Rule Data Model & Template Manager.
Replaces rigid fixed bounding-box templates with Page-Shift-Immune
Anchor-Relative Field Rules (using fuzzy keyword matching + spatial
RIGHT / BELOW extraction + regex patterns for standard formats like PINs).
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from src.config import ROLE_FRONT, ROLE_BACK, NUDGE_STEP

DIRECTION_AUTO = "AUTO"
DIRECTION_RIGHT = "RIGHT"
DIRECTION_BELOW = "BELOW"
DIRECTION_REGEX = "REGEX"
FIELD_DIRECTIONS = [DIRECTION_AUTO, DIRECTION_RIGHT, DIRECTION_BELOW, DIRECTION_REGEX]


@dataclass
class FieldRule:
    """
    Defines an Anchor-Relative Field Extraction Rule for a single Excel column.
    - `name`: Output Excel column header (e.g., 'TD_No', 'PIN', 'Owner_Name').
    - `anchors`: Keyword labels to locate on the page via fuzzy matching (`difflib`).
    - `direction`: Where the value sits relative to the anchor:
        * 'RIGHT' (`dx > 0`, same vertical band)
        * 'BELOW' (`dy > 0`, same horizontal band)
        * 'AUTO' (Inline colon split -> RIGHT -> BELOW -> Regex fallback)
        * 'REGEX' (Full-page regex pattern match regardless of anchor)
    - `regex_pattern`: Optional regular expression to validate or locate standard formats
      (e.g., PIN `###-##-####-###-##` or currency amounts `123,456.00`).
    - `fuzzy_threshold`: Minimum `difflib.SequenceMatcher` similarity ratio (0.0 to 1.0).
    - `role`: Page role ('Page 1 (Front)' or 'Page 2 (Back)').
    """

    name: str
    anchors: List[str] = field(default_factory=list)
    direction: str = DIRECTION_AUTO
    regex_pattern: str = ""
    fuzzy_threshold: float = 0.68
    role: str = ROLE_FRONT

    def to_dict(self) -> Dict[str, Any]:
        """Serialize rule to a JSON-friendly dictionary."""
        return {
            "name": self.name,
            "anchors": list(self.anchors),
            "direction": self.direction,
            "regex_pattern": self.regex_pattern,
            "fuzzy_threshold": round(float(self.fuzzy_threshold), 3),
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FieldRule":
        """Deserialize a FieldRule from a dictionary."""
        raw_anchors = data.get("anchors", [])
        if isinstance(raw_anchors, str):
            anchors = [a.strip() for a in raw_anchors.split(",") if a.strip()]
        else:
            anchors = [str(a).strip() for a in raw_anchors if str(a).strip()]

        direction = str(data.get("direction", DIRECTION_AUTO)).upper()
        if direction not in FIELD_DIRECTIONS:
            direction = DIRECTION_AUTO

        return cls(
            name=str(data.get("name", "Field")).strip(),
            anchors=anchors,
            direction=direction,
            regex_pattern=str(data.get("regex_pattern", "")).strip(),
            fuzzy_threshold=float(data.get("fuzzy_threshold", 0.68)),
            role=str(data.get("role", ROLE_FRONT)),
        )

    def anchors_display(self) -> str:
        """Return comma-separated string of anchor keywords."""
        return ", ".join(self.anchors)


@dataclass
class BoundingBox:
    """
    Represents a normalized [0.0, 1.0] bounding box on a document page.
    Used for visual highlighting of detected OCR blocks, matched anchors, and values.
    """

    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    role: str = ROLE_FRONT
    kind: str = "value"  # 'value', 'anchor', or 'raw'

    def to_coord_dict(self) -> Dict[str, Any]:
        return {
            "x1": round(float(self.x1), 6),
            "y1": round(float(self.y1), 6),
            "x2": round(float(self.x2), 6),
            "y2": round(float(self.y2), 6),
            "role": str(self.role),
            "kind": str(self.kind),
        }

    def nudge(self, dx: float, dy: float) -> None:
        width = max(0.001, self.x2 - self.x1)
        height = max(0.001, self.y2 - self.y1)
        new_x1 = max(0.0, min(1.0 - width, self.x1 + dx))
        new_y1 = max(0.0, min(1.0 - height, self.y1 + dy))
        self.x1 = round(new_x1, 6)
        self.y1 = round(new_y1, 6)
        self.x2 = round(new_x1 + width, 6)
        self.y2 = round(new_y1 + height, 6)


def get_default_faas_rules() -> List[FieldRule]:
    """
    Return the standard pre-configured Field Rules for Philippine FAAS / Tax Declarations.
    """
    return [
        FieldRule(
            name="TD_No",
            anchors=[
                "TD No",
                "T.D. No",
                "Tax Dec No",
                "Tax Declaration No",
                "ARP No",
                "ARP/TD No",
                "TD Number",
            ],
            direction=DIRECTION_AUTO,
            regex_pattern=r"\b(?:ARP|TD|AA|GR|20\d{2})?[-\s]*\d{2,4}[-\s]+\d{2,7}(?:[-\s]+\d{1,6})?\b",
            fuzzy_threshold=0.68,
            role=ROLE_FRONT,
        ),
        FieldRule(
            name="PIN",
            anchors=[
                "PIN",
                "Property Index No",
                "Property Identification No",
                "P.I.N.",
                "Index No",
            ],
            direction=DIRECTION_AUTO,
            regex_pattern=r"\b\d{3}[-\s]?\d{2}[-\s]?\d{3,4}[-\s]?\d{2,3}[-\s]?\d{2,4}(?:[-\s]?\d{1,4})?\b",
            fuzzy_threshold=0.70,
            role=ROLE_FRONT,
        ),
        FieldRule(
            name="Owner_Name",
            anchors=[
                "Owner",
                "Owner's Name",
                "Name of Owner",
                "Declared Owner",
                "Taxpayer",
                "Administrator",
            ],
            direction=DIRECTION_AUTO,
            regex_pattern="",
            fuzzy_threshold=0.68,
            role=ROLE_FRONT,
        ),
        FieldRule(
            name="Lot_No",
            anchors=[
                "Lot No",
                "Lot Number",
                "Survey No",
                "Cad Lot No",
                "Blk No",
                "Cadastral Lot",
            ],
            direction=DIRECTION_AUTO,
            regex_pattern="",
            fuzzy_threshold=0.68,
            role=ROLE_FRONT,
        ),
        FieldRule(
            name="Market_Value",
            anchors=[
                "Market Value",
                "Total Market Value",
                "Base Market Value",
                "Adjusted Market Value",
            ],
            direction=DIRECTION_AUTO,
            regex_pattern=r"(?:PHP|P|₱)?\s*\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\b\d{4,9}\.\d{2}\b",
            fuzzy_threshold=0.68,
            role=ROLE_FRONT,
        ),
        FieldRule(
            name="Assessed_Value",
            anchors=[
                "Assessed Value",
                "Total Assessed Value",
                "Taxable Assessed Value",
                "Assessed",
            ],
            direction=DIRECTION_AUTO,
            regex_pattern=r"(?:PHP|P|₱)?\s*\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\b\d{4,9}\.\d{2}\b",
            fuzzy_threshold=0.68,
            role=ROLE_FRONT,
        ),
    ]


class TemplateManager:
    """
    Manages the ordered collection of Anchor-Relative `FieldRule` definitions
    (and optional visual overlay `BoundingBox` items) for the active workspace.
    """

    def __init__(self, load_defaults: bool = True) -> None:
        self.rules: Dict[str, FieldRule] = {}
        self.boxes: Dict[str, BoundingBox] = {}
        self.single_page_mode: bool = True
        if load_defaults:
            self.reset_to_defaults()

    def reset_to_defaults(self) -> None:
        """Load the default FAAS / Tax Declaration Anchor-Relative Field Rules."""
        self.rules.clear()
        for rule in get_default_faas_rules():
            self.rules[rule.name] = rule

    def add_or_update_rule(
        self,
        name: str,
        anchors: List[str],
        direction: str = DIRECTION_AUTO,
        regex_pattern: str = "",
        fuzzy_threshold: float = 0.68,
        role: str = ROLE_FRONT,
    ) -> FieldRule:
        """Add or update an anchor-relative FieldRule by column name."""
        clean_name = name.strip()
        clean_anchors = [a.strip() for a in anchors if a and a.strip()]
        if not clean_anchors:
            clean_anchors = [clean_name.replace("_", " ")]

        rule = FieldRule(
            name=clean_name,
            anchors=clean_anchors,
            direction=direction if direction in FIELD_DIRECTIONS else DIRECTION_AUTO,
            regex_pattern=regex_pattern.strip(),
            fuzzy_threshold=max(0.40, min(0.98, float(fuzzy_threshold))),
            role=role if role in (ROLE_FRONT, ROLE_BACK) else ROLE_FRONT,
        )
        self.rules[rule.name] = rule
        return rule

    def remove_rule(self, name: str) -> bool:
        """Remove a FieldRule by name."""
        if name in self.rules:
            del self.rules[name]
            return True
        return False

    def clear(self) -> None:
        """Clear all rules and visual boxes."""
        self.rules.clear()
        self.boxes.clear()

    def field_names(self) -> List[str]:
        """Return ordered list of defined Excel column field names."""
        if self.rules:
            return list(self.rules.keys())
        return list(self.boxes.keys())

    def all_rules(self) -> List[FieldRule]:
        """Return ordered list of all FieldRule objects."""
        return list(self.rules.values())

    def snapshot_rules(self) -> List[Dict[str, Any]]:
        """Return a thread-safe serialized list of FieldRule dicts for background OCR."""
        return [r.to_dict() for r in self.rules.values()]

    # Backwards-compatible methods for visual overlay boxes
    def set_detected_overlay_boxes(self, boxes: List[BoundingBox]) -> None:
        """Update the visual overlay boxes shown on the CenterViewport canvas."""
        self.boxes = {f"{b.kind}_{b.name}_{idx}": b for idx, b in enumerate(boxes)}

    def all_boxes(self) -> List[BoundingBox]:
        return list(self.boxes.values())

    def remove_box(self, name: str) -> bool:
        return self.remove_rule(name)

    def save_to_file(self, file_path: str) -> None:
        """Save the Anchor-Relative Field Rules to a JSON file."""
        payload = {
            "single_page_mode": bool(self.single_page_mode),
            "field_rules": [rule.to_dict() for rule in self.rules.values()],
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load_from_file(self, file_path: str) -> int:
        """
        Load Field Rules from a JSON file.
        Also supports converting legacy bounding-box JSON templates into FieldRules.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        new_rules: Dict[str, FieldRule] = {}

        if isinstance(data, dict) and "field_rules" in data and isinstance(data["field_rules"], list):
            self.single_page_mode = bool(data.get("single_page_mode", True))
            for item in data["field_rules"]:
                if isinstance(item, dict):
                    rule = FieldRule.from_dict(item)
                    new_rules[rule.name] = rule
        elif isinstance(data, dict) and "boxes" in data and isinstance(data["boxes"], dict):
            self.single_page_mode = bool(data.get("single_page_mode", True))
            defaults_map = {r.name.lower(): r for r in get_default_faas_rules()}
            for name, coord in data["boxes"].items():
                key = str(name).strip()
                if key.lower() in defaults_map:
                    base = defaults_map[key.lower()]
                    new_rules[key] = FieldRule(
                        name=key,
                        anchors=list(base.anchors),
                        direction=base.direction,
                        regex_pattern=base.regex_pattern,
                        role=str(coord.get("role", ROLE_FRONT)),
                    )
                else:
                    new_rules[key] = FieldRule(
                        name=key,
                        anchors=[key.replace("_", " ")],
                        direction=DIRECTION_AUTO,
                        role=str(coord.get("role", ROLE_FRONT)),
                    )
        else:
            raise ValueError("Unsupported Field Rules JSON format.")

        self.rules = new_rules
        return len(self.rules)

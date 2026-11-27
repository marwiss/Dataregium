# This file was generated with the assistance of an AI coding tool.
"""The difference between two IFC files, element by element, in a process of its own (ADR 0021).

``python -m opencde.ifcgraph.diff A.ifc B.ifc`` pairs the rooted entities of the two files by GlobalId and writes one
JSON document to stdout: the elements only B has (``added``), only A has (``removed``) and those in both whose
attributes differ (``changed``), each named by GlobalId, class, name and storey, and for a changed one the paths that
differ with the old and the new value: a direct attribute by its name, a property as ``Pset.Property``, the placement
as ``ObjectPlacement`` (the 4 by 4 matrix), the shape as ``Representation`` (a fingerprint), the storey as
``Container`` and the type as ``Type``. References to rooted entities compare by GlobalId, other instances by their
structure a few steps deep. Relationships and property sets are folded into the elements they belong to. Nothing
else reaches stdout. Exit code 0 is an answer, 2 an error with a JSON line on stderr.

When every GlobalId in both files has the same STEP id in both (``by: "step"``, never with ``--by-guid``), the files
are also compared instance by instance (``opencde.documents.step.fingerprints``, UL-48, fas 5.1): each instance added,
removed or changed is brought to the nearest objects that reach it, from a relationship to its related objects, and
their entries name it, in ``step_ids`` for b's file and ``step_ids_removed`` for a's; an element whose compared paths
are the same but an instance under it changed is in ``changed`` with no attributes. An instance no object reaches is
in ``unplaced``, and one that reaches more than ``MAX_OWNERS`` objects (an owner history, a style, a unit) in
``shared``, brought to none. Otherwise one file is a re-export (``by: "guid"``), and the answer has no STEP ids.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from ifc_db.worker import keep_stdout_for_the_answer, limit_memory

MAX_ENTRIES = 10_000
MAX_STEP_IDS = 1000  # the STEP ids an entry names; ``step_ids_count`` tells how many there were
MAX_OWNERS = 100  # an instance that reaches more objects is shared, and brought to none
OWNER_DEPTH = 12  # how far back from an instance its objects are looked for: a point of a profile is six steps
DEPTH = 4
# Attributes compared another way, or not at all: the identity and the ownership, the placement and the shape.
SKIPPED = ("type", "GlobalId", "OwnerHistory", "ObjectPlacement", "Representation")
# Rooted entities compared through the elements they describe, not on their own.
FOLDED = ("IfcRelationship", "IfcPropertySetDefinition", "IfcPropertyDefinition")


def _is_instance(value: Any) -> bool:
    return hasattr(value, "is_a") and hasattr(value, "id") and not hasattr(value, "wrappedValue")


def _plain(value: Any, depth: int = DEPTH) -> Any:
    """A value as JSON: a rooted instance by its GlobalId, another by its structure, a typed value by its value."""
    if hasattr(value, "wrappedValue"):
        return _plain(value.wrappedValue, depth)
    if _is_instance(value):
        info = value.get_info(recursive=False, include_identifier=False)
        if info.get("GlobalId"):
            return {"guid": info["GlobalId"]}
        if depth <= 0:
            return {"type": value.is_a()}
        return {
            "type": value.is_a(),
            **{k: _plain(v, depth - 1) for k, v in info.items() if k != "type" and v is not None},
        }
    if isinstance(value, (tuple, list)):
        return [_plain(item, depth) for item in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(_plain(value, DEPTH + 4), sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _who(element: Any) -> dict[str, Any]:
    import ifcopenshell.util.element

    container = ifcopenshell.util.element.get_container(element) if element.is_a("IfcProduct") else None
    return {
        "guid": element.GlobalId,
        "type": element.is_a(),
        "name": getattr(element, "Name", None),
        "storey": container.Name if container is not None and container.is_a("IfcBuildingStorey") else None,
    }


def _paths(element: Any) -> dict[str, Any]:
    """Everything compared about one element, by path."""
    import ifcopenshell.util.element
    import ifcopenshell.util.placement

    paths: dict[str, Any] = {}
    info = element.get_info(recursive=False, include_identifier=False)
    for name, value in info.items():
        if name in SKIPPED:
            continue
        paths[name] = _plain(value)
    if element.is_a("IfcObject"):
        for pset, properties in ifcopenshell.util.element.get_psets(element).items():
            for prop, value in properties.items():
                if prop != "id":
                    paths[f"{pset}.{prop}"] = _plain(value)
        type_object = ifcopenshell.util.element.get_type(element)
        paths["Type"] = type_object.GlobalId if type_object is not None else None
    if element.is_a("IfcProduct"):
        placement = getattr(element, "ObjectPlacement", None)
        if placement is not None and placement.is_a("IfcLocalPlacement"):
            matrix = ifcopenshell.util.placement.get_local_placement(placement)
            paths["ObjectPlacement"] = [[round(float(x), 6) for x in row] for row in matrix.tolist()]
        representation = getattr(element, "Representation", None)
        paths["Representation"] = _fingerprint(representation) if representation is not None else None
        container = ifcopenshell.util.element.get_container(element)
        paths["Container"] = container.GlobalId if container is not None else None
    return paths


def _elements(model: Any) -> dict[str, Any]:
    return {
        entity.GlobalId: entity
        for entity in model.by_type("IfcRoot")
        if not any(entity.is_a(folded) for folded in FOLDED)
    }


def _related(relationship: Any) -> set[str]:
    """The objects a relationship relates: its ``Related…`` attributes."""
    found: set[str] = set()
    for name, value in relationship.get_info(recursive=False, include_identifier=False).items():
        if not name.startswith("Related"):
            continue
        for item in value if isinstance(value, (tuple, list)) else (value,):
            if _is_instance(item) and item.is_a("IfcObjectDefinition"):
                found.add(item.GlobalId)
    return found


def _nearest(model: Any, entity: Any) -> set[str]:
    """The GlobalIds of the nearest objects that reach ``entity`` (its own when it is one), going back through what
    refers to it, and from a relationship to the objects it relates. An owner history is reached by none: every
    rooted entity names one, and its change is no change of the model."""
    found: set[str] = set()
    seen: set[int] = set()
    frontier = [entity]
    for _ in range(OWNER_DEPTH + 1):
        following = []
        for item in frontier:
            if item.id() in seen:
                continue
            seen.add(item.id())
            if item.is_a("IfcObjectDefinition"):
                found.add(item.GlobalId)
            elif item.is_a("IfcRelationship"):
                found.update(_related(item))
            elif not item.is_a("IfcOwnerHistory"):
                following.extend(model.get_inverse(item))
        if found or not following:
            break
        frontier = following
    return found


def _relation_changed(then: Any, now: Any) -> list[tuple[str, str]]:
    """The objects a changed relationship concerns: those it took in (b's file) and let go (a's), so that a wall moved
    out of a storey does not mark the storey's other walls; when it relates the same objects, all of them."""
    before, after = _related(then), _related(now)
    concerned = [(guid, "step_ids") for guid in sorted(after - before)]
    concerned += [(guid, "step_ids_removed") for guid in sorted(before - after)]
    return concerned or [(guid, "step_ids") for guid in sorted(after)]


def _moved(before: Any, after: Any) -> bool:
    """Whether a GlobalId in both files has another STEP id in one of them: a re-export, compared by GlobalId."""
    ids = {entity.GlobalId: entity.id() for entity in before.by_type("IfcRoot")}
    return any(ids.get(entity.GlobalId, entity.id()) != entity.id() for entity in after.by_type("IfcRoot"))


def _instances(a: Path, b: Path) -> tuple[Any, Any, Any] | None:
    """The ids added, removed and changed between the files, compared statement by statement; None when a file has
    ids above what the comparison holds."""
    import numpy as np

    from opencde.documents.step import fingerprints

    with a.open("rb") as stream:
        left = fingerprints(stream)
    with b.open("rb") as stream:
        right = fingerprints(stream)
    if left.over or right.over:
        return None
    size = max(len(left.prints), len(right.prints))
    old, new = np.zeros(size, np.uint64), np.zeros(size, np.uint64)
    old[: len(left.prints)] = np.frombuffer(left.prints, dtype=np.uint64)
    new[: len(right.prints)] = np.frombuffer(right.prints, dtype=np.uint64)
    return (
        np.flatnonzero((old == 0) & (new != 0)),
        np.flatnonzero((old != 0) & (new == 0)),
        np.flatnonzero((old != 0) & (new != 0) & (old != new)),
    )


def _capped(values: list[Any], limit: int, name: str) -> dict[str, Any]:
    """A list cut at ``limit``, and its length as ``<name>_count`` when it was cut."""
    return {name: values[:limit], **({f"{name}_count": len(values)} if len(values) > limit else {})}


def _by_step(before: Any, after: Any, a: Path, b: Path, max_entries: int) -> dict[str, Any] | None:
    """The STEP ids each element was given, by GlobalId, and the instances brought to none or to many; None when
    the files cannot be compared instance by instance."""
    instances = _instances(a, b)
    if instances is None:
        return None
    given: dict[tuple[str, str], list[int]] = {}  # (GlobalId, "step_ids" or "step_ids_removed") to the STEP ids
    loose: dict[str, list[dict[str, Any]]] = {"unplaced": [], "shared": []}
    sides = (("b", after, "added"), ("b", after, "changed"), ("a", before, "removed"))
    order = {"added": 0, "removed": 1, "changed": 2}
    for (side, model, change), ids in zip(sides, (instances[0], instances[2], instances[1]), strict=True):
        name = "step_ids" if side == "b" else "step_ids_removed"
        for step_id in ids.tolist():
            try:
                entity = model.by_id(step_id)
            except RuntimeError:
                entity = None
            if entity is None:
                targets = []
            elif change == "changed" and entity.is_a("IfcRelationship"):
                targets = _relation_changed(before.by_id(step_id), entity)
            else:
                targets = [(guid, name) for guid in _nearest(model, entity)]
            if targets and len(targets) <= MAX_OWNERS:
                for target in targets:
                    given.setdefault(target, []).append(step_id)
                continue
            entry = {"step_id": step_id, "type": entity.is_a() if entity is not None else None, "side": side}
            entry["change"] = change
            if targets:
                loose["shared"].append({**entry, "objects": len(targets)})
            else:
                loose["unplaced"].append(entry)
    entries: dict[str, dict[str, Any]] = {}
    for (guid, name), ids in given.items():
        entries.setdefault(guid, {}).update(_capped(sorted(set(ids)), MAX_STEP_IDS, name))
    answer: dict[str, Any] = {"entries": entries}
    for kind, items in loose.items():
        items.sort(key=lambda item: (item["side"], item["step_id"], order[item["change"]]))
        answer.update(_capped(items, max_entries, kind))
    return answer


def compare(a: Path, b: Path, *, max_entries: int = MAX_ENTRIES, by_guid: bool = False) -> dict[str, Any]:
    import ifcopenshell

    before, after = ifcopenshell.open(str(a)), ifcopenshell.open(str(b))
    old, new = _elements(before), _elements(after)
    step = None if by_guid or _moved(before, after) else _by_step(before, after, a, b, max_entries)
    given: dict[str, dict[str, Any]] = step.pop("entries") if step is not None else {}
    added = [_who(new[guid]) for guid in sorted(set(new) - set(old))]
    removed = [_who(old[guid]) for guid in sorted(set(old) - set(new))]
    changed: list[dict[str, Any]] = []
    same = 0
    for guid in sorted(set(old) & set(new)):
        if old[guid].is_a() != new[guid].is_a():
            changed.append(
                {**_who(new[guid]), "attributes": [{"path": "type", "old": old[guid].is_a(), "new": new[guid].is_a()}]}
            )
            continue
        left, right = _paths(old[guid]), _paths(new[guid])
        differences = [
            {"path": path, "old": left.get(path), "new": right.get(path)}
            for path in sorted(set(left) | set(right))
            if left.get(path) != right.get(path)
        ]
        if differences or guid in given:
            changed.append({**_who(new[guid]), "attributes": differences, **given.get(guid, {})})
        else:
            same += 1
    for entry in (*added, *removed):
        entry.update(given.get(entry["guid"], {}))
    truncated = any(len(entries) > max_entries for entries in (added, removed, changed))
    loose = step or {"unplaced": [], "shared": []}
    return {
        "by": "step" if step is not None else "guid",
        "added": added[:max_entries],
        "removed": removed[:max_entries],
        "changed": changed[:max_entries],
        "same": same,
        "truncated": truncated or "unplaced_count" in loose or "shared_count" in loose,
        **loose,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opencde-diff")
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--max-entries", type=int, default=MAX_ENTRIES)
    parser.add_argument("--by-guid", action="store_true", help="compare by GlobalId alone, as two documents are")
    parser.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    if args.memory_mb:
        limit_memory(args.memory_mb)
    keep_stdout_for_the_answer()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        report = compare(args.a, args.b, max_entries=args.max_entries, by_guid=args.by_guid)
    except Exception as exc:  # the caller reads the last line of stderr
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]}) + "\n")
        return 2
    sys.stdout.write(json.dumps(report, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

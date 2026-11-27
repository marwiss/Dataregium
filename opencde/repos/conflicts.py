# This file was generated with the assistance of an AI coding tool.
"""ifcmerge's conflicts in the form the approval's 412 gives them (SA-99, ADR 0030, UL-48, fas 5.3).

An attribute is named by the schema's name for its place (ifcmerge counts from 1), and a conflict gets the key
``#<step>.<attribute>``, or ``#<step>`` when an instance was deleted on one side or changed its class, which
``keep_theirs`` reads as it reads the approval's keys. ``base``, ``theirs`` and ``ours`` are what the base version,
the latest version and the upload hold, read by the apply worker in the change set's value form; where that cannot be
read, ifcmerge's STEP text stands. The words of ifcmerge's report are never passed on: they quote the files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opencde.ifcgraph.service import IfcGraphService

MAX_CONFLICTS = 1000
DELETED = ("entity_deleted_and_modified", "required_entity_deleted")


def attribute_name(schema: str, entity_class: str, index: int) -> str | None:
    """The schema's name of an entity's attribute at ``index`` (from 1), or None."""
    import ifcopenshell.ifcopenshell_wrapper as wrapper

    try:
        declaration: Any = wrapper.schema_by_name(schema).declaration_by_name(entity_class)  # an entity's, here
        attributes = declaration.all_attributes()
    except (RuntimeError, AttributeError, IndexError):
        return None
    return attributes[index - 1].name() if 0 < index <= len(attributes) else None


def _change(conflict: dict[str, Any], schema: str) -> dict[str, Any]:
    step_id = int(conflict.get("entity_id") or 0)
    kind = str(conflict.get("type") or "")
    if kind in DELETED:
        return {"key": f"#{step_id}", "step_id": step_id, "attribute": None, "op": "delete"}
    if kind == "class_changed":
        return {"key": f"#{step_id}", "step_id": step_id, "attribute": None, "op": "class"}
    index = int(conflict.get("attribute_index") or 0)
    name = attribute_name(schema, str(conflict.get("entity_class") or ""), index) or str(index)
    return {"key": f"#{step_id}.{name}", "step_id": step_id, "attribute": name, "op": "set"}


async def details(
    ifcgraph: IfcGraphService,
    conflicts: list[dict[str, Any]],
    *,
    schema: str,
    files: tuple[tuple[str, str], tuple[str, str], tuple[str, str]],
    guids: dict[int, str],
    latest_index: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """The keys of ``conflicts`` and their details; ``files`` are the base's, the latest version's and the upload's
    storage key and file name, and ``guids`` the rooted entities' GlobalIds by STEP id."""
    changes = [_change(conflict, schema) for conflict in conflicts[:MAX_CONFLICTS]]
    readable = [change for change in changes if change["op"] == "set"]
    values: list[list[dict[str, Any]] | None] = []
    for storage_key, file_name in files:
        try:
            values.append(await ifcgraph.values_of(storage_key, file_name, readable) if readable else [])
        except RuntimeError:
            values.append(None)
    raw = {"base": "base_value", "theirs": "local_value", "ours": "remote_value"}
    keys: list[str] = []
    listed: list[dict[str, Any]] = []
    for conflict, change in zip(conflicts, changes, strict=False):
        place = readable.index(change) if change in readable else None
        detail = {
            "key": change["key"],
            "guid": guids.get(change["step_id"]),
            "path": None,
            "step_id": change["step_id"],
            "attribute": change["attribute"],
            "op": change["op"],
            "version_index": latest_index,
        }
        for side, read in zip(("base", "theirs", "ours"), values, strict=True):
            got = read[place] if read is not None and place is not None and place < len(read) else None
            detail[side] = got["value"] if got and "value" in got else conflict.get(raw[side])
        if change["key"] not in keys:
            keys.append(change["key"])
        listed.append(detail)
    return keys, listed

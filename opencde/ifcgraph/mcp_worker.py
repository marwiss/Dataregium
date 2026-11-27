# This file was generated with the assistance of an AI coding tool.
"""IfcMCP's tools on one IFC file, in a worker process of its own (ADR 0031, F60, F205 d).

``python -m opencde.ifcgraph.mcp_worker <file> <tool> <arguments.json> [--write] [--max-step-id N] [--proposer P]``
opens the file in IfcOpenShell, hands it to IfcMCP's ``IfcSession`` as its model and runs one tool: a reading one
(``READING``) or, on a working copy, an editing one (``EDITING``). ``--write`` writes the file back after an edit,
beside it and then renamed in, with IfcOpenShell's counter raised to the document's highest STEP id first and the
owner histories naming the proposer and this server, as the apply worker does. The answer is one line of JSON on
stdout, ``{"result": …}``, cut to ``--budget`` bytes with ``truncated``; a tool's own failure is ``{"error": …}``.

Nothing the caller gives reaches a path: the server names the file, ``ifc_load``, ``ifc_save``, ``ifc_new``,
``ifc_reset``, the drawing tools and ``ifc_clash`` are not here, ``ifc_edit``, ``ifc_list`` and ``ifc_docs`` see only
the API functions of ``ALLOWED`` that take no IFC file but the model, and ShapeBuilder's methods only those that are
not private and take no path or file.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import sys
import typing
from pathlib import Path
from typing import Any

from ifc_db.worker import keep_stdout_for_the_answer, limit_memory

READING = (
    "ifc_summary",
    "ifc_tree",
    "ifc_info",
    "ifc_select",
    "ifc_relations",
    "ifc_contexts",
    "ifc_materials",
    "ifc_validate",
    "ifc_schedule",
    "ifc_cost",
    "ifc_schema",
)
DISCOVERING = ("ifc_list", "ifc_docs", "ifc_shape_list", "ifc_shape_docs")  # need no model
CHANGING = ("ifc_edit", "ifc_quantify", "ifc_shape")  # change the model, on a working copy alone
EDITING = (*DISCOVERING, *CHANGING)
TOOLS = (*READING, *EDITING)
MAX_ERROR = 300

#: The ``ifcopenshell.api`` functions an AI may run with ``ifc_edit``: making and removing elements, their attributes,
#: properties and quantities, where they stand and what they belong to, their types, materials, classifications,
#: groups and systems, their geometry, openings and fillings, and their styles. A function that is not in the
#: installed IfcOpenShell, or that takes an IFC file but the model (``project.append_asset``), is left out.
ALLOWED = frozenset(
    {
        "root.create_entity",
        "root.copy_class",
        "root.reassign_class",
        "root.remove_product",
        "attribute.edit_attributes",
        "pset.add_pset",
        "pset.edit_pset",
        "pset.remove_pset",
        "pset.add_qto",
        "pset.edit_qto",
        "spatial.assign_container",
        "spatial.unassign_container",
        "spatial.reference_structure",
        "spatial.dereference_structure",
        "aggregate.assign_object",
        "aggregate.unassign_object",
        "nest.assign_object",
        "nest.unassign_object",
        "type.assign_type",
        "type.unassign_type",
        "geometry.add_wall_representation",
        "geometry.add_slab_representation",
        "geometry.add_profile_representation",
        "geometry.add_axis_representation",
        "geometry.add_door_representation",
        "geometry.add_window_representation",
        "geometry.add_railing_representation",
        "geometry.assign_representation",
        "geometry.unassign_representation",
        "geometry.remove_representation",
        "geometry.edit_object_placement",
        "geometry.connect_wall",
        "geometry.disconnect_element",
        "void.add_opening",
        "void.remove_opening",
        "void.add_filling",
        "void.remove_filling",
        "material.add_material",
        "material.add_material_set",
        "material.add_layer",
        "material.add_profile",
        "material.add_constituent",
        "material.assign_material",
        "material.unassign_material",
        "material.edit_layer",
        "material.edit_material",
        "material.remove_material",
        "profile.add_parameterized_profile",
        "profile.add_arbitrary_profile",
        "profile.edit_profile",
        "classification.add_classification",
        "classification.add_reference",
        "classification.remove_reference",
        "group.add_group",
        "group.assign_group",
        "group.unassign_group",
        "group.edit_group",
        "group.remove_group",
        "system.add_system",
        "system.assign_system",
        "system.unassign_system",
        "style.add_style",
        "style.add_surface_style",
        "style.assign_representation_styles",
        "style.assign_material_style",
    }
)
_FILE_WORDS = ("path", "file", "filename", "directory", "folder")


class Refused(ValueError):
    """A call the server does not run; the text is fixed and names no argument's value."""


def _takes_a_file(function: Any) -> bool:
    """Whether a function takes an IFC file, or a path, beside the model it is given first."""
    import ifcopenshell

    try:
        hints = typing.get_type_hints(function)
    except Exception:  # an annotation that does not resolve: judge by the names alone
        hints = {}
    parameters = list(inspect.signature(function).parameters.values())
    for parameter in parameters[1:]:
        hint = hints.get(parameter.name)
        named = parameter.name.lower()
        if hint is ifcopenshell.file or (
            typing.get_origin(hint) is typing.Union and ifcopenshell.file in typing.get_args(hint)
        ):
            return True
        if any(word in named for word in _FILE_WORDS):
            return True
    return False


def allowed_functions() -> list[str]:
    """``ALLOWED`` as the installed IfcOpenShell has it, in order."""
    import importlib

    found = []
    for path in sorted(ALLOWED):
        module, function = path.split(".", 1)
        try:
            imported = importlib.import_module(f"ifcopenshell.api.{module}.{function}")
            if not _takes_a_file(getattr(imported, function)):
                found.append(path)
        except (ImportError, AttributeError, ValueError):
            continue
    return found


def _shape_allowed(method: str) -> bool:
    from ifcopenshell.util.shape_builder import ShapeBuilder

    function = getattr(ShapeBuilder, method, None)
    if method.startswith("_") or not callable(function):
        return False
    names = [parameter.lower() for parameter in inspect.signature(function).parameters]
    return not any(word in name for name in names for word in _FILE_WORDS)


def _listed(result: Any, allowed: list[str], module: str) -> Any:
    """``ifc_list``'s answer cut to what is allowed: the modules with an allowed function, or the allowed functions of
    one module."""
    names = {path.split(".", 1)[1] for path in allowed if path.split(".", 1)[0] == module}
    if module:
        return [entry for entry in result if entry.get("name") in names]
    modules = {path.split(".", 1)[0] for path in allowed}
    kept = []
    for entry in result:
        if entry.get("module") in modules:
            functions = [name for name in entry.get("functions") or [] if f"{entry['module']}.{name}" in allowed]
            kept.append({**entry, "functions": functions, "count": len(functions)})
    return kept


def run(
    path: Path | None, tool: str, arguments: dict[str, Any], *, write: bool, max_step_id: int, proposer: str
) -> Any:
    """One tool on the file at ``path`` (none for the tools that need no model); the edited file is written back
    with ``write``."""
    if tool not in TOOLS:
        raise Refused("the tool is not offered")
    from ifcmcp.core import IfcSession

    session = IfcSession()
    allowed = allowed_functions()
    if tool == "ifc_list":
        module = str(arguments.get("module") or "")
        if module and module not in {name.split(".", 1)[0] for name in allowed}:
            raise Refused("the module has no function an AI may run on this server")
        return _listed(session.ifc_list(module), allowed, module)
    if tool == "ifc_docs":
        if str(arguments.get("function_path")) not in allowed:
            raise Refused("the function is not one an AI may run on this server; ifc_list names them")
        return session.ifc_docs(str(arguments["function_path"]))
    if tool == "ifc_shape_list":
        return [entry for entry in session.ifc_shape_list() if _shape_allowed(str(entry.get("name", "")))]
    if tool == "ifc_shape_docs":
        if not _shape_allowed(str(arguments.get("method"))):
            raise Refused("the method is not one an AI may run on this server; ifc_shape_list names them")
        return session.ifc_shape_docs(str(arguments["method"]))
    if path is None:
        raise Refused("the tool needs a model")
    import ifcopenshell

    model = ifcopenshell.open(str(path))
    if tool in CHANGING:
        if not write:
            raise Refused("an edit needs a working copy: ifc_open makes one")
        if tool == "ifc_edit" and str(arguments.get("function_path")) not in allowed:
            raise Refused("the function is not one an AI may run on this server; ifc_list names them")
        if tool == "ifc_edit":  # IfcEdit converts each argument from its text, as its command line gives them
            given = arguments.get("params") or {}
            arguments = {**arguments, "params": {name: _as_text(value) for name, value in dict(given).items()}}
        if tool == "ifc_shape" and not _shape_allowed(str(arguments.get("method"))):
            raise Refused("the method is not one an AI may run on this server; ifc_shape_list names them")
        return _change(session, model, path, tool, arguments, max_step_id, proposer)
    session.model = model
    return getattr(session, tool)(**arguments)


def _as_text(value: Any) -> str:
    """An argument as IfcEdit reads one: a text as it is, anything else as JSON (a STEP id as its digits)."""
    return value if isinstance(value, str) else json.dumps(value)


def _change(
    session: Any, model: Any, path: Path, tool: str, arguments: dict[str, Any], max_step_id: int, proposer: str
) -> Any:
    """An edit with the document's id history held and the owner histories naming the proposer, written back."""
    from opencde.ifcgraph.ops import ServerChangeSet, _ifc_hold_ids, _ifc_keep_guard

    model = _ifc_hold_ids(model, max_step_id)
    owning = ServerChangeSet(model, proposer)
    previous = owning.owner_settings()
    session.model = model
    try:
        result = getattr(session, tool)(**arguments)
    finally:
        if previous is not None:
            import ifcopenshell.api.owner.settings as settings

            settings.get_user, settings.get_application = previous
    failed = isinstance(result, dict) and result.get("ok") is False
    if not failed:
        _ifc_keep_guard(model)
        written = path.with_name(path.name + ".tmp")
        model.write(str(written))
        os.replace(written, path)
    return result


def _cut(result: Any, budget: int) -> dict[str, Any]:
    """The answer within ``budget`` bytes: a list cut to the entries that fit, anything else whole or not at all."""
    from ifcmcp.core import _jsonify

    data = _jsonify(result)
    whole = json.dumps({"result": data}, separators=(",", ":"), default=str)
    if len(whole.encode("utf-8")) <= budget:
        return {"result": data}
    if isinstance(data, list):
        kept: list[Any] = []
        size = 64
        for entry in data:
            size += len(json.dumps(entry, separators=(",", ":"), default=str).encode("utf-8")) + 1
            if size > budget:
                break
            kept.append(entry)
        return {"result": kept, "truncated": True, "count": len(data)}
    return {"result": None, "truncated": True, "error": "the answer is larger than an answer may be; narrow it down"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opencde-mcp-worker")
    parser.add_argument("file", type=Path)
    parser.add_argument("tool")
    parser.add_argument("arguments", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--no-model", action="store_true")
    parser.add_argument("--max-step-id", type=int, default=0)
    parser.add_argument("--proposer", default="")
    parser.add_argument("--budget", type=int, default=200_000)
    parser.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    if args.memory_mb:
        limit_memory(args.memory_mb)
    keep_stdout_for_the_answer()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        arguments = json.loads(args.arguments.read_text(encoding="utf-8"))
        if not isinstance(arguments, dict):
            raise Refused("the arguments are not an object")
        result = run(
            None if args.no_model else args.file,
            args.tool,
            arguments,
            write=args.write,
            max_step_id=args.max_step_id,
            proposer=args.proposer,
        )
        answer = _cut(result, args.budget)
    except Refused as exc:
        answer = {"error": str(exc), "refused": True}
    except Exception as exc:  # the tool's own failure, which may quote the model: data for the AI, cut
        answer = {"error": f"{type(exc).__name__}: {exc}"[:MAX_ERROR]}
    sys.stdout.write(json.dumps(answer, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

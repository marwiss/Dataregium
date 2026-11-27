# This file was generated with the assistance of an AI coding tool.
"""A stand-in for ifcmerge in the tests (ADR 0030), written here from what ifcmerge is documented to do; ifcmerge
itself is GPL and never here. It merges three files of one instance a line, ``#id=CLASS(...);``, three ways, as
``--prioritise-local`` does: an instance only one side changed takes that side, one both changed alike stays, and
one both changed otherwise is merged attribute by attribute, an attribute both changed otherwise being a conflict;
the other side's new instances move up past main's highest id, with every reference to them. It answers as ifcmerge
does: ``Success!`` and the merged file, or a JSON report over several lines on stdout and exit 1, and settles for
main what ifcmerge settles by itself: an owner history, and an attribute both sides point at another placement or
owner history. An upload that holds CONFLICT gets a report of its own, BROKEN exit 1 alone, and SLOW a wait."""

import json
import re
import sys
import time

INSTANCE = re.compile(r"^#([0-9]+)=(.*);\s*$")
REFERENCE = re.compile(r"#([0-9]+)")

flag, base, theirs, ours, merged = sys.argv[1:6]
assert flag == "--prioritise-local"
text = open(ours, encoding="utf-8").read()
if "CONFLICT" in text:
    report = {
        "status": "failed",
        "message": "Merge failed due to conflicts",
        "conflicts": [
            {
                "type": "attribute_conflict",
                "entity_id": 5,
                "attribute_index": 3,
                "entity_class": "IfcWall",
                "base_value": "'a'",
                "local_value": "'b\x07'",
                "remote_value": "'c'",
            }
        ],
    }
    print(json.dumps(report, indent=2).replace("\\u0007", "\x07"))
    sys.exit(1)
if "BROKEN" in text:
    sys.exit(1)
if "SLOW" in text:
    time.sleep(30)


def read(path):
    """The header lines up to DATA, and the instances by id."""
    head, found = [], {}
    with open(path, encoding="utf-8", newline="") as stream:
        for line in stream:
            matched = INSTANCE.match(line)
            if matched:
                found[int(matched.group(1))] = matched.group(2)
            elif not found:
                head.append(line)
    return head, found


def split(body):
    """The class and the top-level attributes of ``CLASS(a,(b,c),'d,e')``."""
    name, _, inner = body.partition("(")
    inner = inner[:-1]
    parts, depth, quoted, start = [], 0, False, 0
    for index, char in enumerate(inner):
        if char == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(inner[start:index])
            start = index + 1
    parts.append(inner[start:])
    return name, parts


head, b = read(base)
_, t = read(theirs)
_, o = read(ours)
top = max(b, default=0)
offset = max(max(t, default=0) - top, 0)


def moved(body):
    return REFERENCE.sub(
        lambda found: "#" + str(int(found.group(1)) + (offset if int(found.group(1)) > top else 0)), body
    )


o = {(key + offset if key > top else key): moved(body) for key, body in o.items()}
SETTLED = ("IFCLOCALPLACEMENT", "IFCLINEARPLACEMENT", "IFCGRIDPLACEMENT", "IFCOWNERHISTORY")


def settled(name, main, mine):
    """What ifcmerge settles for main by itself: an owner history, and two references to a placement or an owner
    history."""
    if name.upper() == "IFCOWNERHISTORY":
        return True
    refers = [REFERENCE.fullmatch(value.strip()) for value in (main, mine)]
    return all(refers) and all(
        (t.get(int(found.group(1))) or o.get(int(found.group(1))) or "").upper().startswith(SETTLED)
        for found in refers
        if found
    )


def union(name, was, main, mine):
    """A relationship's list both sides changed, as ifcmerge merges it: main's, less what the other side took out,
    with what the other side put in; None for anything else."""

    def members(value):
        value = value.strip()
        if value in ("$", "()"):
            return []
        return value[1:-1].split(",") if value.startswith("(") and value.endswith(")") else None

    lists = [members(value) for value in (was, main, mine)]
    if not name.upper().startswith("IFCREL") or name.upper() == "IFCRELNESTS" or None in lists:
        return None
    before, ours_now, mine_now = lists
    taken = set(before) - set(mine_now)
    kept = [item for item in ours_now if item not in taken]
    return "(" + ",".join(kept + [item for item in mine_now if item not in before and item not in kept]) + ")"


result, conflicts = {}, []
for key in sorted(set(b) | set(t) | set(o)):
    was, main, mine = b.get(key), t.get(key), o.get(key)
    if mine == was:
        chosen = main
    elif main == was or main == mine:
        chosen = mine
    elif main is None or mine is None:
        conflicts.append({"type": "entity_deleted_and_modified", "entity_id": key})
        continue
    else:
        (name, old), (main_name, new_main), (mine_name, new_mine) = split(was or ""), split(main), split(mine)
        if was is None or len({len(old), len(new_main), len(new_mine)}) != 1 or main_name != mine_name:
            conflicts.append({"type": "class_changed", "entity_id": key})
            continue
        attributes = []
        for index, (x, y, z) in enumerate(zip(old, new_main, new_mine, strict=True)):
            if y != x and z != x and y != z and (joined := union(name, x, y, z)) is not None:
                attributes.append(joined)
                continue
            if y != x and z != x and y != z and not settled(name, y, z):
                conflicts.append(
                    {
                        "type": "attribute_conflict",
                        "entity_id": key,
                        "attribute_index": index + 1,
                        "entity_class": name,
                        "base_value": x,
                        "local_value": y,
                        "remote_value": z,
                    }
                )
            attributes.append(z if y == x else y)
        chosen = f"{main_name}({','.join(attributes)})"
    if chosen is not None:
        result[key] = chosen
if conflicts:
    print(
        json.dumps({"status": "failed", "message": "Merge failed due to conflicts", "conflicts": conflicts}, indent=2)
    )
    sys.exit(1)
with open(merged, "w", encoding="utf-8", newline="") as out:
    out.writelines(head)
    out.writelines(f"#{key}={body};\n" for key, body in sorted(result.items()))
    out.write("ENDSEC;\nEND-ISO-10303-21;\n")
print("Success!")

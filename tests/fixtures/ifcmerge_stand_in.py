# This file was generated with the assistance of an AI coding tool.
"""A stand-in for ifcmerge in the tests (ADR 0030): it answers as ifcmerge does, ``Success!`` and the merged file
(here the latest version as it is), or a JSON report over several lines on stdout and exit 1 when the upload
holds CONFLICT, exit 1 alone for BROKEN, and a wait for SLOW. ifcmerge itself is GPL and never here."""

import json
import shutil
import sys
import time

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
print("Success!")
shutil.copyfile(theirs, merged)

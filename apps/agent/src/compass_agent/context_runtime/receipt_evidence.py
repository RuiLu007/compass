"""Reference an opaque tool receipt without guessing its domain or value schema."""

import json
from hashlib import sha256


def receipt(args, payload, refs):
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    identifier = sha256(serialized.encode()).hexdigest()
    yield {
        "kind": "artifact",
        "title": "工具回执",
        "version": identifier,
        "locator": identifier,
        "record": {"artifactId": identifier, "summary": serialized},
        "sourceRefs": refs,
    }

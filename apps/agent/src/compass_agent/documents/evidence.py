"""Map authorized paragraph receipts to citation-ready observations."""


def sections(args, payload, refs):
    for section in payload.get("sections", []):
        source = section["sourceRef"]
        if source not in refs:
            raise ValueError("unregistered source")
        yield {
            "kind": "document" if "text" in section else "candidate",
            "title": section["title"],
            "version": str(section["version"]),
            "locator": section["sectionId"],
            "record": section,
            "sourceRefs": [source],
        }

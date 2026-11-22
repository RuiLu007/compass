"""Run the teacher's lexical retrieval control against the business documents only."""

import json
import re
import sqlite3
from pathlib import Path

CASE = Path(__file__).resolve().parents[1]
TOP_K = 5


def terms(text: str) -> str:
    """Adjacent Chinese character pairs and whole ASCII words for SQLite FTS5."""
    tokens = []
    for word in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9]+", text.lower()):
        if len(word) == 1 or word.isascii():
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return " ".join(tokens)


def load_documents(db: sqlite3.Connection) -> set[tuple[str, str]]:
    db.execute(
        "CREATE VIRTUAL TABLE chunks USING fts5("
        "doc_id UNINDEXED, heading UNINDEXED, title_terms, heading_terms, body_terms)"
    )
    sections = set()
    for path in sorted((CASE / "corpus").glob("*.md")):
        _, metadata, article = path.read_text(encoding="utf-8").split("---\n", 2)
        doc_id = next(
            line.removeprefix("id: ") for line in metadata.splitlines() if line.startswith("id: ")
        )
        title, *parts = re.split(r"^## (.+)$", article, flags=re.MULTILINE)
        for heading, body in zip(parts[::2], parts[1::2], strict=True):
            sections.add((doc_id, heading))
            db.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                (doc_id, heading, terms(title), terms(heading), terms(body)),
            )
    return sections


def search(db: sqlite3.Connection, question: str) -> set[tuple[str, str]]:
    query = " OR ".join(f'"{term}"' for term in dict.fromkeys(terms(question).split()))
    return set(
        db.execute(
            "SELECT doc_id, heading FROM chunks WHERE chunks MATCH ? "
            "ORDER BY bm25(chunks, 0, 0, 1, 2, 1), rowid LIMIT ?",
            (query, TOP_K),
        )
    )


def main() -> None:
    probes = json.loads((CASE / "instructor/probes.json").read_text(encoding="utf-8"))
    db = sqlite3.connect(":memory:")
    sections = load_documents(db)
    complete = hits = total = complete_after = hits_after = 0
    for probe in probes:
        rows = search(db, probe["query"])
        combined = rows
        if followup := probe.get("example_followup"):
            combined = rows | search(db, followup)
        missing = []
        after = 0
        for target in probe["targets"]:
            gold = {(target["doc_id"], heading) for heading in target["any_heading"]}
            if not gold.issubset(sections):
                raise ValueError(f"{probe['id']} has stale headings: {gold - sections}")
            if not gold.intersection(rows):
                missing.append(target["doc_id"])
            after += bool(gold.intersection(combined))
        count = len(probe["targets"])
        hits += count - len(missing)
        total += count
        complete += int(not missing)
        complete_after += int(after == count)
        hits_after += after
        detail = ", ".join(missing) if missing else "全部命中"
        print(f"{probe['id']}  {count - len(missing)}/{count}  {detail}")
    print(
        f"章节数 {len(sections)}，Top-{TOP_K} 目标命中 {hits}/{total}，"
        f"整题覆盖 {complete}/{len(probes)}"
    )
    print(f"含人工示例追问的累计命中 {hits_after}/{total}，整题覆盖 {complete_after}/{len(probes)}")
    db.close()


if __name__ == "__main__":
    main()

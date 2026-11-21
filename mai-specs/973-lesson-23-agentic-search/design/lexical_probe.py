"""讲师用此离线探针比较原词与词表扩展对课程原件前三召回的影响。"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SPEC = Path(__file__).resolve().parents[1] / "spec"
# 本次实验固定已获权、已发布的版本。每个问题在 PRD 指定的来源类别内打分。
SOURCES = {
    "H-0912": ("history", "east-budget-minutes", ("待办 3",)),
    "K-17": ("knowledge", "restart-policy-v2", ("§1 ", "§2 ")),
    "K-08": ("knowledge", "recovery-handbook", ("§2 ",)),
    "E-1009": ("external", "media-offer", ("本期排期",)),
    "E-CAL-2025": ("external", "work-calendar", ("十月调休",)),
    "K-31": ("knowledge", "store-rebate", ("附批",)),
}
ALIASES = {
    "几成": "比例 额度",
    "留着": "预留",
    "一档": "档位",
    "多少钱": "金额",
    "媒介方": "媒体",
    "周六": "星期六",
    "上班": "工作日",
}


@dataclass
class Section:
    doc: str
    heading: str
    fields: list[Counter[str]]


def tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    result = []
    for part in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
        if "\u3400" <= part[0] <= "\u9fff":
            result.extend(part[i : i + 2] for i in range(len(part) - 1))
        else:
            result.append(part)
    return result


def load_sections() -> dict[str, list[Section]]:
    corpus: dict[str, list[Section]] = {}
    for kind, doc, _ in SOURCES.values():
        source = (SPEC / "materials" / kind / f"{doc}.md").read_text(encoding="utf-8")
        parts = re.split(r"^##\s+(.+?)\s*$", source, flags=re.M)
        title = parts[0].splitlines()[0]
        for heading, body in zip(parts[1::2], parts[2::2], strict=True):
            fields = [Counter(tokens(value)) for value in (title, heading, body)]
            corpus.setdefault(kind, []).append(Section(doc, heading, fields))
    return corpus


def read_questions() -> list[tuple[str, str]]:
    lines = iter((SPEC / "PRD.md").read_text(encoding="utf-8").splitlines())
    next(line for line in lines if line.startswith("| 正式问法"))
    next(lines)  # Markdown 表头分隔行
    questions = []
    for line in lines:
        if not line.startswith("|"):
            break
        formal, casual, target = (cell.strip() for cell in line.strip("|").split("|"))
        doc_id = target.split()[0]
        questions.extend([(formal, doc_id), (casual, doc_id)])
    if len(questions) != 12:
        raise ValueError("复核 PRD 探针数量")
    return questions


def top_three(question: str, sections: list[Section], *, expand: bool) -> list[Section]:
    if expand:
        extra_terms = [terms for alias, terms in ALIASES.items() if alias in question]
        question = " ".join([question, *extra_terms])
    terms = sorted(set(tokens(question)))
    averages = [sum(row.fields[i].total() for row in sections) / len(sections) for i in range(3)]
    document_freq = {
        word: sum(any(word in field for field in row.fields) for row in sections) for word in terms
    }

    def score(row: Section) -> float:
        total = 0.0
        for word, frequency in document_freq.items():
            idf = math.log1p((len(sections) - frequency + 0.5) / (frequency + 0.5))
            for i, (field, weight) in enumerate(zip(row.fields, (2, 3, 1), strict=True)):
                tf = field[word]
                length_factor = 0.25 + 0.75 * field.total() / max(averages[i], 1)
                total += weight * idf * (tf * 2.2) / (tf + 1.2 * length_factor)
        return total

    ranked = sorted(
        ((score(section), section) for section in sections),
        key=lambda item: (-item[0], item[1].doc, item[1].heading),
    )
    return [section for value, section in ranked if value > 0][:3]


def main() -> None:
    corpus = load_sections()
    questions = read_questions()
    for expand in (False, True):
        misses = []
        for query, doc_id in questions:
            kind, target, headings = SOURCES[doc_id]
            candidates = top_three(query, corpus[kind], expand=expand)
            # 模拟读原文时打开命中段落和相邻段，并检查所需条款是否齐全。
            target_sections = [section for section in corpus[kind] if section.doc == target]
            hit = False
            for candidate in candidates:
                if candidate.doc != target or not candidate.heading.startswith(headings):
                    continue
                i = target_sections.index(candidate)
                context = target_sections[max(0, i - 1) : i + 2]
                if all(any(section.heading.startswith(h) for section in context) for h in headings):
                    hit = True
                    break
            if not hit:
                misses.append(query)
        label = "原词与词表" if expand else "仅原词"
        print(f"{label}  {len(questions) - len(misses)}/{len(questions)}")
        for query in misses:
            print(f"  漏召回  {query}")


if __name__ == "__main__":
    main()

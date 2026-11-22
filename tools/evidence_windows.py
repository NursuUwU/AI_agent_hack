"""Select short, attributed excerpts from local evidence snapshots."""

import re


def evidence_window(text: str, query: str, limit: int) -> str:
    if len(text) <= limit:
        return text.rstrip()

    words = set(re.findall(r"[\w-]{4,}", query.lower()))
    lines = text.splitlines()
    ranked = sorted(range(len(lines)), key=lambda i: (
        -len(words & set(re.findall(r"[\w-]{4,}", lines[i].lower()))), i))
    selected = {}
    remaining = limit
    for i in ranked:
        if i in selected or not lines[i].strip():
            continue
        for j in (i, i - 1, i + 1):
            if j in selected or not 0 <= j < len(lines) or not lines[j].strip():
                continue
            prefix = f"{j + 1}: "
            line = lines[j]
            if len(prefix) + len(line) + 1 <= remaining:
                selected[j] = prefix + line
                remaining -= len(selected[j]) + 1
            elif not selected and j == i:
                selected[j] = prefix + line[:remaining - len(prefix) - 3] + "..."
                remaining = 0
        if remaining < 128:
            break
    return "\n".join(selected[i] for i in sorted(selected))

#!/usr/bin/env python3
"""Compare a supplied reference with transcript text or a Lecorder result JSON.

NFKC/case normalization, punctuation ignored; CER additionally ignores spaces.
Scores measure agreement with the supplied reference, not factual correctness.
"""

import argparse
import json
import re
import unicodedata
from pathlib import Path


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[^\w\s]|_", " ", text).split())


def alignment(reference, hypothesis):
    # Cells hold distance, substitutions, deletions, insertions. O(hypothesis)
    # memory avoids retaining a quadratic matrix for full-length lectures.
    previous = [(i, 0, 0, i) for i in range(len(hypothesis) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, actual in enumerate(hypothesis, 1):
            if expected == actual:
                current.append(previous[j - 1])
                continue
            cost, sub, delete, insert = previous[j - 1]
            choices = [(cost + 1, sub + 1, delete, insert)]
            cost, sub, delete, insert = previous[j]
            choices.append((cost + 1, sub, delete + 1, insert))
            cost, sub, delete, insert = current[j - 1]
            choices.append((cost + 1, sub, delete, insert + 1))
            current.append(min(choices, key=lambda cell: cell[0]))
        previous = current
    distance, sub, delete, insert = previous[-1]
    return {
        "reference_units": len(reference),
        "substitutions": sub,
        "deletions": delete,
        "insertions": insert,
        "error_rate": distance / len(reference) if reference else None,
    }


def evaluate(reference, hypothesis):
    expected, actual = normalize(reference), normalize(hypothesis)
    if not expected:
        raise ValueError("Reference must contain words or characters")
    return {
        "wer": alignment(expected.split(), actual.split()),
        "cer": alignment(expected.replace(" ", ""), actual.replace(" ", "")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="UTF-8 verified reference text")
    parser.add_argument(
        "hypothesis", type=Path, help="UTF-8 text or result JSON with text"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    hypothesis = args.hypothesis.read_text(encoding="utf-8")
    if args.hypothesis.suffix.lower() == ".json":
        hypothesis = json.loads(hypothesis)["text"]
    result = evaluate(args.reference.read_text(encoding="utf-8"), hypothesis)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

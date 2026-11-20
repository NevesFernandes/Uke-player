#!/usr/bin/env python3
"""Convert Uke Tabs 'Song Code' (with <crd> tags) to a readable monospace view.

- Chord tags are stripped; spaces are kept, so chord/lyric alignment is preserved
  (tags are zero-width in the app, the spaces carry the column positions).
- Riff/tab blocks (lines like 'A|--3--5--|') are passed through byte-for-byte.
- Output can be wrapped in a fenced code block so Notion renders it monospaced.

Usage:
    python3 songcode_readable.py input.txt            # prints converted text
    python3 songcode_readable.py input.txt --fence    # wrapped in ``` for Notion
    python3 songcode_readable.py input.txt --check    # also prints warnings
"""
import re
import sys

CRD_TAG = re.compile(r"</?crd>")
# A tab line: string name (A/E/C/G, optionally lowercase), pipe, then fret chars. Allow
# leading whitespace and trailing repeat markers like 'X4' / 'x2'.
TAB_LINE = re.compile(r"^\s*[AECGaecg]\|[-0-9()hpbrx/\\~|\s]*(\|)?\s*([xX]\d+)?\s*$")
# Tab string name glued onto other text on the same line (flattened paste).
FLAT_TAB = re.compile(r"[AECG]\|[-0-9()]{4,}\|?[AECG]\|")


def is_tab_line(line: str) -> bool:
    return bool(TAB_LINE.match(line))


def convert(text: str):
    warnings = []
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if is_tab_line(line):
            out.append(line)  # verbatim, never touched
            continue
        if FLAT_TAB.search(line):
            warnings.append(f"line {n}: tab data flattened into text; needs manual cleanup")
        out.append(CRD_TAG.sub("", line).rstrip())
    result = "\n".join(out)
    leftovers = re.findall(r"</?crd", result)
    if leftovers:
        warnings.append("leftover <crd tags after conversion")
    if "?" in result and re.search(r"\w\?\w", result):
        warnings.append("possible broken apostrophes ('?' inside words)")
    return result, warnings


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        sys.exit(__doc__)
    with open(args[0], encoding="utf-8") as f:
        text = f.read()
    result, warnings = convert(text)
    if "--fence" in flags:
        result = "```\n" + result + "\n```"
    print(result)
    if "--check" in flags:
        for w in warnings:
            print("WARNING:", w, file=sys.stderr)


if __name__ == "__main__":
    main()

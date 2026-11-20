#!/usr/bin/env python3
"""Transpose a Uke Tabs 'Song Code' (<crd> tags + 4-string tab blocks) by N semitones (any N).

Chords: roots (and slash basses) shifted, quality kept, sharps/flats style kept.
  - Chord-over-lyric lines keep their columns (padded/consumed spaces) so chords stay
    over the same syllable.
  - Bracket/inline lines ([Em][G] x4, chords inside lyrics) are replaced in place.

Tabs (rows top->bottom A E C G, LOW-G tuning G3 C4 E4 A4):
  Placement rule (keeps the hand where it was):
    1. Try moving EVERY note in the song by the same number of strings k (-3..+3).
       The new fret is whatever gives the exact new pitch. A k is valid if all notes
       stay on the neck (fret 0..max). Cost = total fret displacement. Lowest cost wins
       (ties -> smaller |k|). Same k for all riffs keeps their shapes consistent.
       (Em->Am, +5, comes out as: one string up, same fret, C->E string +1.)
    2. If no single k works for all riffs, choose a k per riff block.
    3. If even that fails, place each note on its own closest-fret spot (shape not
       preserved; reported). Blocks with technique marks (h p / ~ ...) are then left
       untouched and reported instead.
  Two-digit frets like (10) are read and written; columns stay aligned. Every note is
  pitch-asserted (new pitch == old pitch + N). max fret defaults to 12 (or the highest
  fret already in the song, if higher).

Usage: python3 songcode_transpose.py input.txt --semitones N [--max-fret 12] [--fence]
"""
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import songcode_readable as sr  # noqa: E402

SHARP = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]
FLAT = ["A", "Bb", "B", "C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab"]
IDX = {n: i for i, n in enumerate(SHARP)}
IDX.update({n: i for i, n in enumerate(FLAT)})

TAG_CHORD = re.compile(r"<crd>(.*?)</crd>")
CHORD = re.compile(r"^([A-G][#b]?)(.*?)(?:/([A-G][#b]?))?$")
TAB_ROW = re.compile(r"^(\s*)([AECGaecg])\|([^|]*)(\|.*)?$")
TOKEN = re.compile(r"\(\d+\)|\d|.", re.S)
M1, M2 = "\x01", "\x02"  # chord markers, turned into <crd> tags (or removed) at the end


def vis(text):
    return len(text.replace(M1, "").replace(M2, ""))

STRINGS = ["G", "C", "E", "A"]          # index 0..3, lowest pitch first
OPEN = [55, 60, 64, 69]                  # MIDI of open strings, low-G ukulele
ROWS = ["A", "E", "C", "G"]              # tab rows, top to bottom


# ---------------------------------------------------------------- chords
def _tr_note(name, n):
    table = FLAT if "b" in name else SHARP
    return table[(IDX[name] + n) % 12]


def transpose_chord(text, n):
    m = CHORD.match(text.strip())
    if not m:
        return text  # not a chord (e.g. N.C.) -> untouched
    root, qual, bass = m.groups()
    out = _tr_note(root, n) + qual
    if bass:
        out += "/" + _tr_note(bass, n)
    return out


def transpose_chord_line(line, n, warnings, lineno):
    matches = list(TAG_CHORD.finditer(line))
    if not matches:
        return line
    stripped, pos, spans = "", 0, []
    for m in matches:
        stripped += line[pos:m.start()]
        spans.append((len(stripped), len(m.group(1)), transpose_chord(m.group(1), n)))
        stripped += m.group(1)
        pos = m.end()
    stripped += line[pos:]
    if "[" in stripped:  # bracket / inline style: plain in-place replacement
        for start, old, new in reversed(spans):
            stripped = stripped[:start] + M1 + new + M2 + stripped[start + old:]
        return stripped
    out, cursor = "", 0  # aligned chord line: keep each chord's start column
    for start, old, new in spans:
        out += stripped[cursor:start]
        if vis(out) > start:
            warnings.append(f"line {lineno}: chord pushed right (not enough room)")
        out += M1 + new + M2
        cursor = start + old
        if len(new) < old:
            out += " " * (old - len(new))
        elif len(new) > old:
            need = len(new) - old
            run = len(stripped[cursor:]) - len(stripped[cursor:].lstrip(" "))
            more_text = bool(stripped[cursor + run:].strip())
            can = max(0, run - 1) if more_text else run  # always keep >=1 space before next chord
            take = min(need, can)
            cursor += take
            if need > take:
                warnings.append(f"line {lineno}: chord wider than gap, later chords shifted")
    return out + stripped[cursor:]


# ------------------------------------------------------------------ tabs
@dataclass
class Note:
    s: int      # string index (0=G .. 3=A)
    col: int    # start column in the row body
    fret: int
    w: int      # printed width (1, or len('(10)'))


@dataclass
class Block:
    parsed: list
    width: int
    notes: list
    marks: list  # (string idx, col, char) technique marks that travel with their row


def parse_block(rows):
    parsed = [TAB_ROW.match(r) for r in rows]
    if [m.group(2).upper() for m in parsed] != ROWS:
        return None, "strings are not A E C G"
    width = max(len(m.group(3)) for m in parsed)
    notes, marks = [], []
    for m in parsed:
        s = STRINGS.index(m.group(2).upper())
        for t in TOKEN.finditer(m.group(3)):
            tok = t.group()
            if tok == "-":
                continue
            if len(tok) > 1:
                notes.append(Note(s, t.start(), int(tok[1:-1]), len(tok)))
            elif tok.isdigit():
                notes.append(Note(s, t.start(), int(tok), 1))
            else:
                marks.append((s, t.start(), tok))
    starts = {(nt.s, nt.col) for nt in notes}
    for nt in notes:  # nothing may start inside a wide '(NN)' token
        for c in range(nt.col + 1, nt.col + nt.w):
            if any(x.col == c for x in notes) or any(c == mk[1] for mk in marks):
                return None, "notes overlap a (NN) fret"
    return Block(parsed, width, notes, marks), None


def place(note, k, n, maxfret):
    ns = note.s + k
    if not 0 <= ns < 4:
        return None
    nf = OPEN[note.s] + note.fret + n - OPEN[ns]
    return (ns, nf) if 0 <= nf <= maxfret else None


def choose_k(notes, n, maxfret):
    best = None
    for k in sorted(range(-3, 4), key=lambda k: (abs(k), k)):
        cost, ok = 0, True
        for nt in notes:
            p = place(nt, k, n, maxfret)
            if p is None:
                ok = False
                break
            cost += abs(p[1] - nt.fret)
        if ok and (best is None or cost < best[1]):
            best = (k, cost)
    return best


def place_free(note, n, maxfret):
    best = None
    for ns in range(4):
        nf = OPEN[note.s] + note.fret + n - OPEN[ns]
        if 0 <= nf <= maxfret:
            key = (abs(nf - note.fret), abs(ns - note.s), ns)
            if best is None or key < best[0]:
                best = (key, (ns, nf))
    return best[1] if best else None


def fret_text(nf):
    return str(nf) if nf < 10 else f"({nf})"


def render_block(blk, placements, mark_k):
    cells = {}  # (new string idx, col) -> text
    for nt, (ns, nf) in zip(blk.notes, placements):
        if (ns, nt.col) in cells:
            return None, "two notes landed on the same string and column"
        cells[(ns, nt.col)] = fret_text(nf)
    for s, col, ch in blk.marks:
        ns = s + mark_k
        if (ns, col) in cells:
            return None, "technique mark collides with a note"
        cells[(ns, col)] = ch
    old_w, new_w = {}, {}
    for nt, (ns, nf) in zip(blk.notes, placements):
        old_w[nt.col] = max(old_w.get(nt.col, 1), nt.w)
        new_w[nt.col] = max(new_w.get(nt.col, 1), len(fret_text(nf)))
    bodies = {i: "" for i in range(4)}
    c = 0
    while c < blk.width:
        if c in old_w:
            e = max(old_w[c], new_w[c])
            for i in range(4):
                bodies[i] += cells.get((i, c), "-").ljust(e, "-")
            c += old_w[c]
        else:
            for i in range(4):
                bodies[i] += cells.get((i, c), "-")
            c += 1
    out = []
    for m in blk.parsed:
        i = STRINGS.index(m.group(2).upper())
        out.append(f"{m.group(1)}{m.group(2)}|{bodies[i]}{m.group(4) or ''}")
    return out, None


def transpose_tabs(chunks, n, maxfret_base, warnings, info):
    """chunks: list of (lineno, [4 rows]). Returns list of replacement row lists."""
    parsed = []
    for lineno, rows in chunks:
        blk, err = parse_block(rows)
        if err:
            warnings.append(f"line {lineno}: {err}; tab block left untouched")
        parsed.append(blk)
    all_notes = [nt for b in parsed if b for nt in b.notes]
    if not all_notes or n == 0:
        return [rows for _, rows in chunks]
    maxfret = max(maxfret_base, max(nt.fret for nt in all_notes))
    gk = choose_k(all_notes, n, maxfret)
    if gk:
        info.append(f"tabs: all riffs moved {gk[0]:+d} string(s), total fret shift {gk[1]} "
                    f"(max fret {maxfret})")
    else:
        warnings.append("tabs: no single string shift fits every riff; chosen per riff")
    result = []
    for (lineno, rows), blk in zip(chunks, parsed):
        if blk is None or not blk.notes:
            result.append(rows)
            continue
        k, mode = (gk[0], "global") if gk else (None, None)
        if k is None:
            ck = choose_k(blk.notes, n, maxfret)
            if ck:
                k, mode = ck[0], "per-riff"
        if k is not None:
            placements = [place(nt, k, n, maxfret) for nt in blk.notes]
            mark_k = k
        else:
            if blk.marks:
                warnings.append(f"line {lineno}: no placement fits and block has technique "
                                f"marks; left untouched (manual review)")
                result.append(rows)
                continue
            placements = [place_free(nt, n, maxfret) for nt in blk.notes]
            if any(p is None for p in placements):
                warnings.append(f"line {lineno}: a note goes off the neck at this shift; "
                                f"left untouched (manual review)")
                result.append(rows)
                continue
            mark_k = 0
            warnings.append(f"line {lineno}: no single string shift fits; notes placed one by "
                            f"one (shape not preserved)")
        for nt, (ns, nf) in zip(blk.notes, placements):  # pitch check
            assert OPEN[ns] + nf == OPEN[nt.s] + nt.fret + n, (nt, ns, nf)
        out, err = render_block(blk, placements, mark_k)
        if err:
            warnings.append(f"line {lineno}: {err}; left untouched (manual review)")
            result.append(rows)
        else:
            result.append(out)
    return result


# -------------------------------------------------------------- whole song
def transpose(text, n, max_fret=12, tagged=False):
    warnings, info = [], []
    lines = text.splitlines()
    plan, i = [], 0  # plan: ('line', text) | ('tab', chunk_index)
    chunks = []
    while i < len(lines):
        if sr.is_tab_line(lines[i]):
            j = i
            while j < len(lines) and sr.is_tab_line(lines[j]):
                j += 1
            if (j - i) % 4 == 0:
                for a in range(i, j, 4):
                    chunks.append((a + 1, lines[a:a + 4]))
                    plan.append(("tab", len(chunks) - 1))
            else:
                warnings.append(f"line {i + 1}: {j - i} tab rows is not a multiple of 4; untouched")
                plan.extend(("line", l) for l in lines[i:j])
            i = j
            continue
        if sr.FLAT_TAB.search(lines[i]):
            warnings.append(f"line {i + 1}: tab data flattened into text; needs manual cleanup")
        plan.append(("line", transpose_chord_line(lines[i], n, warnings, i + 1).rstrip()))
        i += 1
    chord_warn = [w for w in warnings if "chord " in w]
    warnings = [w for w in warnings if "chord " not in w]
    if chord_warn:
        where = ", ".join(w.split(":")[0].replace("line ", "") for w in chord_warn[:6])
        warnings.append(f"{len(chord_warn)} chord line(s): a transposed chord is wider than its gap, "
                        f"so later chords on that line shift right (lines {where}"
                        f"{', ...' if len(chord_warn) > 6 else ''})")
    new_chunks = transpose_tabs(chunks, n, max_fret, warnings, info)
    out = []
    for kind, val in plan:
        if kind == "tab":
            out.extend(new_chunks[val])
        else:
            out.append(val)
    joined = "\n".join(out)
    if tagged:
        joined = joined.replace(M1, "<crd>").replace(M2, "</crd>")
    else:
        joined = joined.replace(M1, "").replace(M2, "")
    return joined, warnings, info


def main():
    argv = sys.argv[1:]
    n, max_fret, fence, tagged = 5, 12, "--fence" in argv, "--tagged" in argv
    if "--semitones" in argv:
        n = int(argv[argv.index("--semitones") + 1])
    if "--max-fret" in argv:
        max_fret = int(argv[argv.index("--max-fret") + 1])
    skip = {str(n), str(max_fret)}
    files = [a for a in argv if not a.startswith("--") and a not in skip]
    if not files:
        sys.exit(__doc__)
    text = open(files[0], encoding="utf-8").read()
    result, warnings, info = transpose(text, n, max_fret, tagged)
    print("```\n" + result + "\n```" if fence else result)
    for x in info:
        print("INFO:", x, file=sys.stderr)
    for w in warnings:
        print("WARNING:", w, file=sys.stderr)


if __name__ == "__main__":
    main()

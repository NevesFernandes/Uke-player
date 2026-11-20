#!/usr/bin/env python3
"""Build songs.json from songs_raw.json.

songs_raw.json : [{"id", "title", "artist", "code"}]  (code = raw Uke Tabs Song Code, with <crd> tags)
songs.json     : same songs with the readable text pre-transposed for every shift in SHIFTS,
                 so the web player only has to pick a variant (no transposition logic in JS).

Usage:  python3 build_songs.py [--raw songs_raw.json] [--out songs.json] [--min -6] [--max 6]
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from songcode_transpose import transpose  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="songs_raw.json")
    ap.add_argument("--out", default="songs.json")
    ap.add_argument("--min", type=int, default=-6)
    ap.add_argument("--max", type=int, default=6)
    a = ap.parse_args()

    raw = json.load(open(a.raw, encoding="utf-8"))
    songs = []
    for s in raw:
        code = s["code"].replace("\r\n", "\n")
        shifts, warnings = {}, {}
        for n in range(a.min, a.max + 1):
            text, warns, _info = transpose(code, n)
            shifts[str(n)] = text
            if warns:
                warnings[str(n)] = warns
        songs.append({
            "id": s["id"], "title": s["title"], "artist": s.get("artist", ""),
            "shifts": shifts, "warnings": warnings,
        })
        base = warnings.get("0", [])
        print(f"{s['title']}: {len(shifts)} variants" + (f", warnings at 0: {base}" if base else ""))
    songs.sort(key=lambda x: x["title"].lower())
    out = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "min": a.min, "max": a.max, "songs": songs}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {a.out} ({os.path.getsize(a.out)//1024} KB, {len(songs)} songs)")


if __name__ == "__main__":
    main()

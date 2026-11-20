#!/usr/bin/env python3
"""Pull songs from the Notion 'Projects' database into songs_raw.json (stdlib only).

Setup (once):
  1. https://www.notion.so/profile/integrations -> new internal integration -> copy the secret.
  2. Open the 'Ukulele Covers' / Projects database in Notion -> ... -> Connections -> add your integration.
  3. export NOTION_TOKEN=secret_xxx      (never commit it)

Run:  python3 sync_notion.py [--exclude "The Suburbs"] && python3 build_songs.py

NOTE: written against the classic Notion API (version 2022-06-28). Not tested against your workspace yet.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.request

DATABASE_ID = "178565b7618781d2b38efb0769ead577"   # Projects database
TITLE_PROP, CODE_PROP = "Project name", "Song Code"


def api(path, body, token):
    req = urllib.request.Request(
        "https://api.notion.com/v1" + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def plain(rich):
    return "".join(t.get("plain_text", "") for t in rich)


def slug(s):
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="songs_raw.json")
    ap.add_argument("--exclude", action="append", default=[], help="title substring to skip (repeatable)")
    a = ap.parse_args()
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        sys.exit("Set NOTION_TOKEN first (see the docstring).")

    songs, cursor = [], None
    while True:
        body = {"filter": {"property": CODE_PROP, "rich_text": {"is_not_empty": True}}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = api(f"/databases/{DATABASE_ID}/query", body, token)
        for page in res["results"]:
            props = page["properties"]
            name = plain(props[TITLE_PROP]["title"]).strip()
            code = plain(props[CODE_PROP]["rich_text"])
            if not name or not code.strip() or any(x.lower() in name.lower() for x in a.exclude):
                continue
            title, _, artist = name.partition(" - ")
            songs.append({"id": slug(title), "title": title.strip(), "artist": artist.strip(), "code": code})
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=1)
    print(f"{len(songs)} songs -> {a.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
AppMatchup — App Store data generator.

Keeps every comparison page's "At a glance" table in sync from one data file,
so an app that appears on several pages can never disagree with itself.

    python3 scripts/appdata.py fetch    refresh figures from the App Store
    python3 scripts/appdata.py check    show what build would change, write nothing
    python3 scripts/appdata.py build    rewrite the tables in place

Run from the repo root. Requires only the Python standard library.

The eight At a glance fields are frozen. Seven come from the App Store and are
overwritten by `fetch`. The Price field is hand-written per app in apps.json,
because subscription pricing lives on the developer's own pricing page and the
App Store API doesn't know it. If you change a price, change it there.

Each page carries a generated block delimited by:

    <!-- APPDATA:START -->
    ...
    <!-- APPDATA:END -->

Anything between those markers is machine-written and will be overwritten.
Everything else in the file is yours.
"""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "apps.json"

START = "<!-- APPDATA:START -->"
END = "<!-- APPDATA:END -->"

LOOKUP = "https://itunes.apple.com/lookup?id={ids}&country=us"

FIELDS = [
    "Developer",
    "Price",
    "App Store rating",
    "Current version",
    "First released",
    "Install size",
    "Minimum iOS",
    "iPad support",
]


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def save(d):
    DATA.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("—", "&mdash;")
        .replace("’", "&rsquo;")
    )


def fetch(d):
    ids = ",".join(str(a["track_id"]) for a in d["apps"].values())
    with urllib.request.urlopen(LOOKUP.format(ids=ids), timeout=30) as r:
        results = {x["trackId"]: x for x in json.load(r)["results"]}

    missing = [k for k, a in d["apps"].items() if a["track_id"] not in results]
    if missing:
        sys.exit(f"App Store returned nothing for: {', '.join(missing)}. Nothing written.")

    changes = []
    seeded_any = False
    for key, app in d["apps"].items():
        r = results[app["track_id"]]
        new = {
            "developer": r["sellerName"],
            "rating": round(r.get("averageUserRating", 0), 2),
            "rating_count": r.get("userRatingCount", 0),
            "version": r.get("version", ""),
            "version_updated": r.get("currentVersionReleaseDate", "")[:10],
            "released": r.get("releaseDate", "")[:10],
            "size_mb": round(int(r.get("fileSizeBytes", 0)) / 1_000_000, 1),
            "min_ios": r.get("minimumOsVersion", ""),
            "ipad": any("iPad" in s for s in r.get("supportedDevices", [])),
        }
        old = app.get("auto") or {}
        if not old:
            seeded_any = True
        for f, v in new.items():
            if f in old and old[f] != v:
                changes.append(f"  {app['display_name']}: {f}  {old[f]} -> {v}")
        app["auto"] = new

    d["fetched_at"] = date.today().isoformat()

    if seeded_any:
        print("Seeded data for apps that had none.")
    if changes:
        print("Changed since last fetch:")
        print("\n".join(changes))
        print("\nReview these before running build — a price or tier change may")
        print("also need the Price field and the prose updated by hand.")
    if not seeded_any and not changes:
        print("No changes since last fetch.")
    print(f"fetched_at set to {d['fetched_at']}")
    return d


def row(label, left, right, apps):
    la, ra = apps
    return (
        "          <tr>\n"
        f'            <th scope="row">{esc(label)}</th>\n'
        f'            <td data-app="{esc(la["data_app"])}">{left}</td>\n'
        f'            <td data-app="{esc(ra["data_app"])}">{right}</td>\n'
        "          </tr>"
    )


def render(d, page):
    apps = [d["apps"][k] for k in d["pages"][page]]
    a, b = apps
    aa, ba = a["auto"], b["auto"]

    def pair(f):
        return f(a, aa), f(b, ba)

    values = [
        ("Developer", *pair(lambda app, x: esc(x["developer"]))),
        ("Price", *pair(lambda app, x: esc(app["price_display"]))),
        ("App Store rating", *pair(lambda app, x: f'{x["rating"]:.2f} from {x["rating_count"]:,} ratings')),
        ("Current version", *pair(lambda app, x: f'{esc(x["version"])}, updated {x["version_updated"]}')),
        ("First released", *pair(lambda app, x: x["released"])),
        ("Install size", *pair(lambda app, x: f'{x["size_mb"]} MB')),
        ("Minimum iOS", *pair(lambda app, x: esc(x["min_ios"]))),
        ("iPad support", *pair(lambda app, x: "Yes" if x["ipad"] else "No")),
    ]

    rows = "\n".join(row(lbl, l, r, apps) for lbl, l, r in values)
    when = d["fetched_at"]

    return (
        f"{START}\n"
        '      <table class="spec">\n'
        "        <thead>\n"
        "          <tr>\n"
        '            <th scope="col" class="spec-field">Field</th>\n'
        f'            <th scope="col">{esc(a["display_name"])}</th>\n'
        f'            <th scope="col">{esc(b["display_name"])}</th>\n'
        "          </tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{rows}\n"
        "        </tbody>\n"
        "      </table>\n\n"
        f'      <p class="caption">Pulled from the App Store on {when}. Prices, versions, '
        "ratings, install sizes and OS requirements change &mdash; check the current App "
        "Store listing before relying on any of these values.</p>\n"
        f"      {END}"
    )


def apply(d, write):
    pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.S)
    touched, skipped = [], []

    for page in d["pages"]:
        path = ROOT / page
        if not path.exists():
            skipped.append(f"{page}: file not found")
            continue
        html = path.read_text(encoding="utf-8")
        if START not in html or END not in html:
            skipped.append(f"{page}: markers missing — add {START} / {END} around the At a glance table")
            continue
        new = pattern.sub(lambda m: render(d, page), html, count=1)
        if new == html:
            touched.append(f"{page}: no change")
        else:
            touched.append(f"{page}: table updated")
            if write:
                path.write_text(new, encoding="utf-8")

    for line in touched:
        print(("  " if write else "  would be: ") + line)
    for line in skipped:
        print("  SKIPPED " + line)
    if skipped:
        sys.exit(1)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        save(fetch(load()))
    elif cmd == "check":
        apply(load(), write=False)
    elif cmd == "build":
        apply(load(), write=True)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()

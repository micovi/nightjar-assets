#!/usr/bin/env python3
"""Check and re-target the metadata documents of both worked examples.

Three subcommands, none of which needs a node, a devnet or a network:

    tools/doc.py verify                     every digest, every limit, the uri budget
    tools/doc.py retarget --base <url>      point both documents at a different fork
    tools/doc.py digests                    recompute pon/c.json's digests from pon/art/

`verify` is the one to run first and after every edit. It is deliberately the only thing in
this repository that reads a document and says whether it is *self-consistent*; nothing here
can say whether a document is **true**, because that is a property of the channel and is
checked against the channel (see the repository README).

A digest that does not verify is worse than no digest at all: `spec/asset-collection-v0.md`
section 3.3 requires a wallet to discard a mismatching image without retrying, so the failure
is silent and the artwork simply disappears. Publishing one is a way of breaking a picture
that no wallet will report.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The canonical base this repository's documents are written against. `retarget` replaces it.
DEFAULT_BASE = "https://raw.githubusercontent.com/micovi/nightjar-assets/main/"

# `spec/transition-v0.md` section 9: an ASSET message's `uri` is at most 255 bytes of US-ASCII.
# It is the ceiling of the wire format rather than a chosen number: `uri_len` is a `u8`, so
# nothing larger can be expressed on the wire at all.
URI_MAX = 255
# `spec/asset-metadata-v0.md` section 2.1: `#b2=` plus 43 base64url characters.
PIN_COST = len("#b2=") + 43

# `spec/asset-metadata-v0.md` section 3.2.
DOC_MAX = 16 * 1024
LOGO_MAX = 256 * 1024
PER_ASSET_MAX = 512 * 1024

# The document each example's ASSET messages point at. These paths are inside the uri budget,
# which is why they are this short; the repository README does the arithmetic.
DOCS = {
    "nc": "nc/a.json",
    "pon": "pon/c.json",
}


def b2(path: pathlib.Path) -> str:
    """BLAKE2b-256, base64url, unpadded — `logo.b2` and `digests[i]` both use this."""
    d = hashlib.blake2b(path.read_bytes(), digest_size=32).digest()
    return base64.urlsafe_b64encode(d).decode().rstrip("=")


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text())


def dump(rel: str, doc: dict) -> None:
    (ROOT / rel).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def base_of(doc: dict) -> str | None:
    """The base every absolute URL in a document shares, read back out of the document."""
    urls = []
    if "logo" in doc:
        urls.append(doc["logo"]["uri"])
    if "item" in doc:
        urls.append(doc["item"]["image"])
    if not urls:
        return None
    for suffix in ("nc/logo.png", "pon/logo.png", "pon/art/{index}.png"):
        for u in urls:
            if u.endswith(suffix):
                return u[: -len(suffix)]
    return None


class Report:
    def __init__(self) -> None:
        self.bad = 0

    def ok(self, msg: str) -> None:
        print(f"  ok    {msg}")

    def fail(self, msg: str) -> None:
        self.bad += 1
        print(f"  FAIL  {msg}")

    def check(self, cond: bool, msg: str) -> None:
        (self.ok if cond else self.fail)(msg)


def verify(base: str) -> int:
    r = Report()

    print("uri budget — spec/transition-v0.md section 9, spec/asset-metadata-v0.md section 2.1")
    for name, rel in DOCS.items():
        url = base + rel
        pinned = len(url) + PIN_COST
        r.check(
            pinned <= URI_MAX,
            f"{name}: {len(url)} + {PIN_COST} pin = {pinned} of {URI_MAX} bytes  ({url})",
        )

    print("nc/a.json — spec/asset-metadata-v0.md")
    nc = load("nc/a.json")
    nc_bytes = (ROOT / "nc/a.json").stat().st_size
    r.check(nc_bytes <= DOC_MAX, f"document {nc_bytes} of {DOC_MAX} bytes")
    r.check(nc["schema"] == "nightjar-asset-metadata/1", f"schema {nc['schema']}")
    logo = ROOT / "nc/logo.png"
    r.check(nc["logo"]["b2"] == b2(logo), "logo.b2 matches nc/logo.png")
    r.check(logo.stat().st_size <= LOGO_MAX, f"logo {logo.stat().st_size} of {LOGO_MAX} bytes")
    r.check(nc["logo"]["uri"] == base + "nc/logo.png", f"logo.uri is under {base}")

    print("pon/c.json — spec/asset-collection-v0.md")
    pon = load("pon/c.json")
    pon_bytes = (ROOT / "pon/c.json").stat().st_size
    r.check(pon_bytes <= DOC_MAX, f"document {pon_bytes} of {DOC_MAX} bytes")
    r.check(pon["schema"] == "nightjar-collection-metadata/1", f"schema {pon['schema']}")
    plogo = ROOT / "pon/logo.png"
    r.check(pon["logo"]["b2"] == b2(plogo), "logo.b2 matches pon/logo.png")
    r.check(plogo.stat().st_size <= LOGO_MAX, f"logo {plogo.stat().st_size} of {LOGO_MAX} bytes")
    r.check(pon["logo"]["uri"] == base + "pon/logo.png", f"logo.uri is under {base}")
    r.check(
        pon["item"]["image"] == base + "pon/art/{index}.png",
        f"item.image is under {base}",
    )
    r.check("{index}" in pon["item"]["name"], "item.name carries the {index} token")

    digests = pon.get("digests", [])
    size = pon.get("size")
    r.check(len(digests) == size, f"{len(digests)} digests for a declared size of {size}")
    missing, bad = [], []
    total = 0
    for i, want in enumerate(digests):
        art = ROOT / f"pon/art/{i}.png"
        if not art.exists():
            missing.append(i)
            continue
        total += art.stat().st_size
        if b2(art) != want:
            bad.append(i)
    r.check(not missing, f"every digest has an image  (missing: {missing or 'none'})")
    r.check(not bad, f"every image matches its digest  (mismatched: {bad or 'none'})")
    # section 2.1 asks a wallet to fetch every member's artwork once any member is accepted;
    # section 3.2's 512 KiB per-asset total is what makes that affordable or not.
    r.check(
        total <= PER_ASSET_MAX,
        f"all {len(digests)} artworks total {total} bytes, against the {PER_ASSET_MAX}-byte per-asset limit",
    )

    print()
    print("FAILED" if r.bad else "ok — both documents are self-consistent.")
    print("Self-consistent is not true: identity is the asset_id on the channel. See README.md.")
    return 1 if r.bad else 0


def retarget(new: str) -> int:
    if not new.endswith("/"):
        new += "/"
    for rel in DOCS.values():
        doc = load(rel)
        old = base_of(doc)
        if old is None:
            print(f"{rel}: no absolute URL to retarget", file=sys.stderr)
            continue
        text = (ROOT / rel).read_text()
        (ROOT / rel).write_text(text.replace(old, new))
        print(f"{rel}: {old} -> {new}")
    print()
    print("The documents' bytes changed, so every `#b2=` pin computed from the old bytes is stale.")
    print("Publish them, then mint: the pin is computed from the published document (nc/mint.sh).")
    return 0


def digests() -> int:
    doc = load("pon/c.json")
    doc["digests"] = [b2(ROOT / f"pon/art/{i}.png") for i in range(doc["size"])]
    dump("pon/c.json", doc)
    print(f"pon/c.json: {len(doc['digests'])} digests recomputed from pon/art/")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check every digest, every limit and the uri budget")
    v.add_argument("--base", default=None, help=f"base URL the documents are published under (default: read from the documents)")
    rt = sub.add_parser("retarget", help="point both documents at a different fork")
    rt.add_argument("--base", required=True, help="e.g. https://raw.githubusercontent.com/you/nightjar-assets/main/")
    sub.add_parser("digests", help="recompute pon/c.json's digests from pon/art/")
    a = ap.parse_args()

    if a.cmd == "verify":
        base = a.base or base_of(load("pon/c.json")) or DEFAULT_BASE
        if not base.endswith("/"):
            base += "/"
        print(f"base {base}")
        return verify(base)
    if a.cmd == "retarget":
        return retarget(a.base)
    return digests()


if __name__ == "__main__":
    sys.exit(main())

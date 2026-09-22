#!/usr/bin/env python3
"""Check and re-target the metadata documents of both worked examples.

Four subcommands, none of which needs a node, a running channel or a network:

    tools/doc.py verify                     every digest, every limit, the uri budget
    tools/doc.py retarget --base <url>      point both documents at a different fork
    tools/doc.py digests                    recompute pon/c.json's digests from pon/art/
    tools/doc.py digests --table            the same digests as pon/d.bin, out of the document
    tools/doc.py selftest                   round-trip the table form in a scratch copy

`verify` is the one to run first and after every edit. It is deliberately the only thing in
this repository that reads a document and says whether it is *self-consistent*; nothing here
can say whether a document is **true**, because that is a property of the channel and is
checked against the channel (see the repository README).

A digest that does not verify is worse than no digest at all: `spec/asset-collection-v0.md`
section 3.3 requires a wallet to discard a mismatching image without retrying, so the failure
is silent and the artwork simply disappears. Publishing one is a way of breaking a picture
that no wallet will report.

**Two shapes carry the same digests.** A collection may pin its artwork with an inline
`digests` array or with a `digest_table` naming a separate file (`spec/asset-collection-v0.md`
sections 3.3 and 3.3.1), and the two are mutually exclusive. The array is right for this
repository — a hundred members is a long way inside the 302 the 16 KiB document limit allows —
and `--table` exists because the *other* shape needs to be exercised by something before a
publisher with ten thousand pieces is the first person to run it. `selftest` is that something.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import pathlib
import shutil
import sys
import tempfile

# Where the documents live. `selftest` rebinds it at a scratch copy, which is the only reason
# it is a module global rather than a constant: every check below reads it, so pointing it
# somewhere else runs the *same* checks against different bytes rather than a second copy of
# them that could drift.
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

# `spec/asset-collection-v0.md` section 3.3.1: the table is exactly `32 × count` raw bytes and
# nothing else — no magic number, no version byte, no header. The document already carries the
# version in `schema` and `revision`, and `digest_table.b2` binds the bytes, so a file of the
# right length that is not the table (an HTML error page of exactly 3 200 bytes, say) fails the
# digest rather than parsing as something. `digests[i]` is the 32 bytes at offset `32i`.
TABLE_ENTRY = 32

# The document each example's ASSET messages point at. These paths are inside the uri budget,
# which is why they are this short; the repository README does the arithmetic.
DOCS = {
    "nc": "nc/a.json",
    "pon": "pon/c.json",
}

# Where `--table` writes the table, and where `verify` looks for it. One character of name, for
# the same reason `c.json` is one character: see the budget note in `verify`.
TABLE_REL = "pon/d.bin"


def enc(digest: bytes) -> str:
    """base64url, unpadded — the encoding of every digest in these documents."""
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def b2_bytes(data: bytes) -> str:
    """BLAKE2b-256 of bytes already in hand — `digest_table.b2` hashes a whole file this way."""
    return enc(hashlib.blake2b(data, digest_size=32).digest())


def b2(path: pathlib.Path) -> str:
    """BLAKE2b-256, base64url, unpadded — `logo.b2` and `digests[i]` both use this."""
    return b2_bytes(path.read_bytes())


def raw_b2(path: pathlib.Path) -> bytes:
    """The same digest as `b2`, unencoded — what goes into the table, which is not text."""
    return hashlib.blake2b(path.read_bytes(), digest_size=32).digest()


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
    if "digest_table" in doc:
        urls.append(doc["digest_table"]["uri"])
    if not urls:
        return None
    for suffix in ("nc/logo.png", "pon/logo.png", "pon/art/{index}.png", TABLE_REL):
        for u in urls:
            if u.endswith(suffix):
                return u[: -len(suffix)]
    return None


def put_digest_member(doc: dict, key: str, value) -> dict:
    """Swap `digests` for `digest_table` or back, without moving it in the document.

    Popping one member and assigning the other would put the new one at the end, after `links`,
    and the diff would then be the whole tail of the file rather than the one member that
    changed. The two are alternatives for the same thing (section 3.3.1), so they belong in the
    same place, and a reader comparing the two shapes should be looking at one hunk.
    """
    out: dict = {}
    placed = False
    for k, v in doc.items():
        if k in ("digests", "digest_table"):
            if not placed:
                out[key] = value
                placed = True
            continue
        out[k] = v
        # A document that has neither yet: the array sits after the template it pins.
        if k == "item" and "digests" not in doc and "digest_table" not in doc:
            out[key] = value
            placed = True
    if not placed:
        out[key] = value
    return out


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


def table_digests(r: Report, table: dict, base: str) -> list[str] | None:
    """Check a `digest_table` and read the digests back out of it, or `None` if it cannot be.

    `None` is not an empty list and the difference is the whole of section 3.3.1's last rule. A
    table that cannot be read leaves **every** member unpinned, and an empty list would walk into
    the loop below this and print `mismatched: none` — a line that reads as reassurance for a
    collection that has just lost all of its pinning.

    The order of the three checks is the order `spec/asset-collection-v0.md` section 3.3.1 puts
    them in, and it is not arbitrary. Length first, because a file of the wrong length has no
    well-defined entry at offset `32i` and reading one is reading whatever happens to be there.
    Then `b2`, which the spec requires **before a single digest is read** — an unpinned table is
    a swap vector for every image at once, so a table that does not hash to what the document
    says is not a table with some bad entries, it is a file of unknown provenance.

    The per-entry comparison that follows is a *publisher's* diagnostic and not a wallet's
    behaviour: a wallet whose `b2` check fails treats the whole collection as unpinned and stops.
    Whoever is about to publish this wants to know which index went wrong, so it runs anyway —
    after the failure above it, never instead of it.
    """
    count = table.get("count")
    path = ROOT / TABLE_REL
    want_len = TABLE_ENTRY * count if isinstance(count, int) else None

    r.check(
        table.get("uri") == base + TABLE_REL,
        f"digest_table.uri is {base + TABLE_REL}",
    )

    if not path.exists():
        r.fail(f"the document carries a digest_table and {TABLE_REL} is not here")
        return None

    blob = path.read_bytes()
    r.check(
        want_len is not None and len(blob) == want_len,
        f"{TABLE_REL} is {len(blob)} bytes, and {TABLE_ENTRY} x count of {count} is {want_len}",
    )
    if want_len is None or len(blob) != want_len:
        # No entry is at a known offset any more, so there is nothing further to read here.
        return None

    r.check(b2_bytes(blob) == table.get("b2"), f"digest_table.b2 matches {TABLE_REL}")

    return [enc(blob[i * TABLE_ENTRY : (i + 1) * TABLE_ENTRY]) for i in range(count)]


def verify(base: str) -> int:
    r = Report()

    pon = load("pon/c.json")
    table = pon.get("digest_table")

    print("uri budget — spec/transition-v0.md section 9, spec/asset-metadata-v0.md section 2.1")
    budgeted = dict(DOCS)
    if table is not None:
        # The table's uri is not itself signed by an ASSET message — it is pinned by `b2` inside
        # the document, which *is* signed — but it is held to the same budget anyway, and the
        # reason is that pon/mint.sh pins it: it fetches the published table and hashes it before
        # the document that commits to it is pinned. Budgeting it here means a path that will not
        # fit is found by `verify`, with nothing sent, rather than by a mint script standing in
        # front of a naming message that can only be sent once.
        budgeted["pon table"] = TABLE_REL
    for name, rel in budgeted.items():
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

    # Which of the two shapes is this, and is it only one of them? Section 3.3.1 forbids both,
    # and a wallet that finds both must ignore `digests` and use the table — so a publisher who
    # ships both has made an error the wallet silently *papers over*, which is exactly the kind
    # this file exists to catch while it is still a local diff.
    inline = pon.get("digests")
    size = pon.get("size")
    r.check(
        not (inline is not None and table is not None),
        "the document carries digests or digest_table, not both  (section 3.3.1)",
    )

    if table is not None:
        print(f"pon/{TABLE_REL.split('/')[-1]} — section 3.3.1, the table form")
        want = table_digests(r, table, base)
        art = len(list((ROOT / "pon/art").glob("*.png")))
        r.check(
            table.get("count") == art,
            f"digest_table.count is {table.get('count')} for {art} artworks in pon/art/",
        )
        r.check(
            table.get("count") == size,
            f"digest_table.count is {table.get('count')} for a declared size of {size}",
        )
    else:
        want = inline or []
        r.check(len(want) == size, f"{len(want)} digests for a declared size of {size}")

    if want is None:
        # Section 3.3.1: a failure of the length or of `b2` leaves the whole collection
        # unpinned, not some of its members. Saying that once is the honest report; running the
        # loop below over nothing would print two reassuring lines about zero digests.
        r.fail("no digest can be read from the table, so every member is unpinned  (section 3.3.1)")
        want = []
        print()
        print("FAILED")
        print("Self-consistent is not true: identity is the asset_id on the channel. See README.md.")
        return 1

    missing, bad = [], []
    total = 0
    for i, expect in enumerate(want):
        art = ROOT / f"pon/art/{i}.png"
        if not art.exists():
            missing.append(i)
            continue
        total += art.stat().st_size
        if b2(art) != expect:
            bad.append(i)
    r.check(not missing, f"every digest has an image  (missing: {missing or 'none'})")
    r.check(not bad, f"every image matches its digest  (mismatched: {bad or 'none'})")
    # section 2.1 asks a wallet to fetch every member's artwork once any member is accepted;
    # section 3.2's 512 KiB per-asset total is what makes that affordable or not.
    r.check(
        total <= PER_ASSET_MAX,
        f"all {len(want)} artworks total {total} bytes, against the {PER_ASSET_MAX}-byte per-asset limit",
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
        # A blunt replace over the whole file rather than a walk of the members, so that a uri
        # added to the format later — `digest_table.uri` was the last one — is retargeted by a
        # tool nobody remembered to teach about it. `base_of` still has to know the new member,
        # because it is what reads the *old* base back out; the rewrite does not.
        (ROOT / rel).write_text(text.replace(old, new))
        print(f"{rel}: {old} -> {new}")
    print()
    print("The documents' bytes changed, so every `#b2=` pin computed from the old bytes is stale.")
    print("Publish them, then mint: the pin is computed from the published document (nc/mint.sh).")
    return 0


def digests(table: bool) -> int:
    """Recompute pon/c.json's digests from pon/art/, inline or as a table.

    The two shapes carry identical bytes and differ only in where they are written down:
    `digests[i]` base64url in the document, or the raw 32 bytes at offset `32i` of a file the
    document pins with one `b2` (`spec/asset-collection-v0.md` sections 3.3 and 3.3.1). Whichever
    is written, the other is removed — they are mutually exclusive, and leaving the old one
    behind produces a document a wallet is required to read *past* rather than reject.
    """
    doc = load("pon/c.json")
    size = doc["size"]
    base = base_of(doc) or DEFAULT_BASE
    raw = [raw_b2(ROOT / f"pon/art/{i}.png") for i in range(size)]

    if not table:
        doc = put_digest_member(doc, "digests", [enc(d) for d in raw])
        dump("pon/c.json", doc)
        (ROOT / TABLE_REL).unlink(missing_ok=True)
        print(f"pon/c.json: {size} digests recomputed from pon/art/, inline")
        return 0

    blob = b"".join(raw)
    (ROOT / TABLE_REL).write_bytes(blob)
    doc = put_digest_member(
        doc,
        "digest_table",
        {"uri": base + TABLE_REL, "b2": b2_bytes(blob), "count": size},
    )
    dump("pon/c.json", doc)
    print(f"{TABLE_REL}: {len(blob)} bytes = {TABLE_ENTRY} x {size}, b2 {doc['digest_table']['b2']}")
    print(f"pon/c.json: digests replaced by a digest_table, {(ROOT / 'pon/c.json').stat().st_size} bytes")
    print()
    print("Publish the table FIRST and the document second: the document commits to the table's")
    print("b2, so a document published against a table that is not up yet pins bytes nobody will")
    print("be served. pon/mint.sh checks that before it signs anything.")
    return 0


# -------------------------------------------------------------------------------------------
# selftest — because the committed documents do not exercise the table form
#
# `pon/c.json` carries an inline `digests` array and should: a hundred members is well inside
# the 302 that `spec/asset-collection-v0.md` section 3.3 allows, and the table would trade one
# file for two in exchange for nothing. So the code that writes and reads a table is code this
# repository never runs in anger — and code nobody runs is code that works until the first
# person who needs it, who will be a publisher with ten thousand pieces and a naming message
# they can only send once.
#
# This builds the table form in a scratch copy, checks it says the same thing as the inline form
# byte for byte, and then breaks it in the two ways section 3.3.1 names — a flipped byte and a
# short file — to check that `verify` says so rather than reading past it.


class Selftest:
    def __init__(self) -> None:
        self.bad = 0

    def check(self, cond: bool, msg: str) -> None:
        print(f"  {'ok   ' if cond else 'FAIL '} {msg}")
        if not cond:
            self.bad += 1


def _verify_quietly(base: str) -> tuple[int, str]:
    """Run the real `verify`, capturing what it printed. The point of the selftest is that the
    checks it exercises are the ones a publisher runs, so it calls that function and not a
    convenient reimplementation of it."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = verify(base)
    return rc, buf.getvalue()


def selftest() -> int:
    global ROOT
    home = ROOT
    base = base_of(load("pon/c.json")) or DEFAULT_BASE
    inline_before = load("pon/c.json")["digests"]
    t = Selftest()

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "nightjar-assets"
        work.mkdir(parents=True)
        for d in ("nc", "pon"):
            shutil.copytree(home / d, work / d)
        ROOT = work
        try:
            print(f"scratch copy at {work}")

            print("the table form round-trips")
            digests(table=True)
            rc, out = _verify_quietly(base)
            t.check(rc == 0, f"verify passes on the table form  ({out.count('  ok ')} checks)")

            doc = load("pon/c.json")
            blob = (work / TABLE_REL).read_bytes()
            t.check("digests" not in doc, "the inline digests array is gone from the document")
            t.check(
                doc["digest_table"]["count"] == len(inline_before),
                f"digest_table.count is {doc['digest_table']['count']}",
            )
            t.check(
                len(blob) == TABLE_ENTRY * len(inline_before),
                f"the table is {len(blob)} bytes = {TABLE_ENTRY} x {len(inline_before)}",
            )
            same = [
                enc(blob[i * TABLE_ENTRY : (i + 1) * TABLE_ENTRY]) == want
                for i, want in enumerate(inline_before)
            ]
            t.check(
                all(same),
                f"all {len(same)} table entries equal the inline digests they replace",
            )
            t.check(
                doc["digest_table"]["b2"] == b2_bytes(blob),
                "digest_table.b2 is the digest of the whole table",
            )

            print("a flipped byte is caught")
            # Byte 0 of entry 0: the smallest possible edit, and the one a corrupted transfer or
            # a host rewriting one image would produce. It must fail `b2` before it fails
            # anything else — the table is pinned as a whole, not entry by entry.
            broken = bytearray(blob)
            broken[0] ^= 0x01
            (work / TABLE_REL).write_bytes(bytes(broken))
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                "FAIL  digest_table.b2 matches" in out,
                "and says the table does not hash to what the document pins",
            )
            t.check(
                "mismatched: [0]" in out,
                "and says which index it was, which is what a publisher needs",
            )

            print("a truncated table is caught")
            # One byte short. This is the check that has to come first: every entry after the
            # cut is still readable at a plausible offset, so a length check skipped here means
            # digests read from the wrong place rather than an error.
            (work / TABLE_REL).write_bytes(blob[:-1])
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                f"FAIL  {TABLE_REL} is {len(blob) - 1} bytes" in out,
                f"and says so as a length, not as {TABLE_ENTRY * len(inline_before) // TABLE_ENTRY} bad digests",
            )
            t.check(
                "FAIL  no digest can be read from the table" in out,
                "and reads no digest out of it at all, so every member is unpinned",
            )
            t.check(
                "mismatched: none" not in out,
                "and does not print a reassuring line about the zero digests it read",
            )

            print("carrying both members is caught")
            (work / TABLE_REL).write_bytes(blob)
            doc = load("pon/c.json")
            doc["digests"] = inline_before
            dump("pon/c.json", doc)
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                "FAIL  the document carries digests or digest_table, not both" in out,
                "and says the two members are mutually exclusive",
            )

            print("and back to the inline form")
            doc = load("pon/c.json")
            del doc["digests"]
            dump("pon/c.json", doc)
            digests(table=False)
            rc, out = _verify_quietly(base)
            t.check(rc == 0, "verify passes on the inline form")
            t.check(
                load("pon/c.json")["digests"] == inline_before,
                "and the digests are the ones the repository committed",
            )
            t.check(
                not (work / TABLE_REL).exists(),
                f"and {TABLE_REL} is gone, so no document can be read past to find it",
            )
        finally:
            ROOT = home

    print()
    print("FAILED" if t.bad else "ok — the table form carries the same digests as the array.")
    print("Nothing outside the scratch copy was touched; the repository still ships `digests`.")
    return 1 if t.bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check every digest, every limit and the uri budget")
    v.add_argument("--base", default=None, help=f"base URL the documents are published under (default: read from the documents)")
    rt = sub.add_parser("retarget", help="point both documents at a different fork")
    rt.add_argument("--base", required=True, help="e.g. https://raw.githubusercontent.com/you/nightjar-assets/main/")
    dg = sub.add_parser("digests", help="recompute pon/c.json's digests from pon/art/")
    dg.add_argument(
        "--table",
        action="store_true",
        help=f"write them to {TABLE_REL} and carry a digest_table instead (section 3.3.1)",
    )
    sub.add_parser("selftest", help="round-trip the table form in a scratch copy; changes nothing here")
    a = ap.parse_args()

    if a.cmd == "verify":
        base = a.base or base_of(load("pon/c.json")) or DEFAULT_BASE
        if not base.endswith("/"):
            base += "/"
        print(f"base {base}")
        return verify(base)
    if a.cmd == "retarget":
        return retarget(a.base)
    if a.cmd == "selftest":
        return selftest()
    return digests(a.table)


if __name__ == "__main__":
    sys.exit(main())

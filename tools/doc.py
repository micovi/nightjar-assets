#!/usr/bin/env python3
"""Check and re-target the metadata documents of both worked examples.

Five subcommands, none of which needs a node, a running channel or a network:

    tools/doc.py verify                     every digest, every limit, the uri budget
    tools/doc.py retarget --base <url>      point both documents at a different fork
    tools/doc.py digests                    recompute pon/c.json's digests from pon/art/
    tools/doc.py digests --table            the same digests as pon/d.bin, out of the document
    tools/doc.py items                      move pon/i.json's items back into the document
    tools/doc.py items --external           the same items as pon/i.json, out of the document
    tools/doc.py selftest                   round-trip both external forms in a scratch copy

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

**And two shapes carry the same items.** `items` inline costs about 190 bytes a member, which
runs a trait-bearing collection out of the 16 KiB document at 81 — so section 3.4.1 lets the
array live in a file of its own, pinned by an `items_document` whose `b2` and `bytes` the
document carries. This repository ships neither external form and `--external` is here for the
same reason `--table` is: the shape that the documents committed here do not exercise is the
shape that breaks, and it breaks for the publisher who could least afford it.
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
DEFAULT_BASE = "https://raw.githubusercontent.com/micovi/nyctis-assets/main/"

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

# Where `items --external` writes the items array, and where `verify` looks for it. One character
# again, and for the same reason: `items_document.uri` is budgeted against the same 255 bytes.
ITEMS_REL = "pon/i.json"

# `spec/asset-collection-v0.md` section 3.4: what an `items` entry may carry. `index` selects the
# member; the rest override the template for it. A member outside this set is one a wallet is
# *required* to ignore (`asset-metadata-v0.md` section 4.4), which is exactly why an unrecognized
# one is reported here rather than passed on: a misspelt `image` is not an error anywhere
# downstream, it is a picture that silently does not override and nothing that says so.
ITEM_MEMBERS = ("index", "name", "description", "image", "b2", "attributes")
ATTRS_MAX = 16
ATTR_STR_MAX = 48

# The two pairs of mutually exclusive members, each naming the inline shape first.
DIGEST_PAIR = ("digests", "digest_table")
ITEMS_PAIR = ("items", "items_document")


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
    if "items_document" in doc:
        urls.append(doc["items_document"]["uri"])
    if not urls:
        return None
    for suffix in ("nc/logo.png", "pon/logo.png", "pon/art/{index}.png", TABLE_REL, ITEMS_REL):
        for u in urls:
            if u.endswith(suffix):
                return u[: -len(suffix)]
    return None


def put_member(doc: dict, pair: tuple[str, str], key: str, value, anchor: str | None) -> dict:
    """Swap one member of a mutually exclusive pair for the other, without moving it.

    Popping one member and assigning the other would put the new one at the end, after `links`,
    and the diff would then be the whole tail of the file rather than the one member that
    changed. The two are alternatives for the same thing (sections 3.3.1 and 3.4.1), so they
    belong in the same place, and a reader comparing the two shapes should be looking at one
    hunk. `anchor` is the member the new one follows in a document that carries neither yet.
    """
    out: dict = {}
    placed = False
    for k, v in doc.items():
        if k in pair:
            if not placed:
                out[key] = value
                placed = True
            continue
        out[k] = v
        if k == anchor and not any(p in doc for p in pair):
            out[key] = value
            placed = True
    if not placed:
        out[key] = value
    return out


def items_anchor(doc: dict) -> str | None:
    """Where `items` goes in a document carrying neither shape of it: after the digests it
    accompanies, because that is the order section 3.4 follows section 3.3 in and a document
    whose members run in the spec's order is one a reader can check against the spec."""
    return next((k for k in ("digest_table", "digests", "item") if k in doc), None)


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


def document_items(r: Report, spec: dict, base: str) -> list | None:
    """Check an `items_document` and read the array back out of it, or `None` if it cannot be.

    The order of the checks is the order `spec/asset-collection-v0.md` section 3.4.1 puts them
    in, and as with the table it is not arbitrary.

    **Length first, against the number the document declares.** `bytes` is there so a wallet
    knows what the fetch costs *before* it makes it and can refuse rather than discover the size
    by having downloaded it, and the rule that makes the declaration worth anything is that a
    body of any other length is refused. So a `bytes` that is merely stale — the right file, one
    character longer — is as fatal as a swapped one, and a publisher wants to hear that as a
    length and not as a digest.

    **Then `b2`, and it is required for the reason `digest_table.b2` is required.** Per-member
    names, traits and image overrides sitting in an unpinned second file are a swap vector for
    every one of them at once; the document is signed through the `uri` an ASSET message carries
    and this file is reached only from inside it.

    **Only then is it JSON.** A file that is not the items document — a login page from a host
    that has started asking for one — fails on the length or on the digest rather than on a
    parse error, which is the failure a publisher can do something about.
    """
    path = ROOT / ITEMS_REL
    declared = spec.get("bytes")
    sized = isinstance(declared, int) and not isinstance(declared, bool)

    r.check(spec.get("uri") == base + ITEMS_REL, f"items_document.uri is {base + ITEMS_REL}")

    if not path.exists():
        r.fail(f"the document carries an items_document and {ITEMS_REL} is not here")
        return None

    blob = path.read_bytes()
    r.check(
        sized and len(blob) == declared,
        f"{ITEMS_REL} is {len(blob)} bytes, and items_document.bytes declares {declared}",
    )
    if not (sized and len(blob) == declared):
        # A wallet refuses a body whose length is not exactly `bytes`; it does not read it and
        # decide afterwards, which is the whole point of declaring the number. Nothing below
        # this line is a check a wallet would still be running, so nothing below it runs.
        return None

    r.check(b2_bytes(blob) == spec.get("b2"), f"items_document.b2 matches {ITEMS_REL}")

    try:
        parsed = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        r.fail(f"{ITEMS_REL} does not parse as JSON: {e}")
        return None
    if not isinstance(parsed, list):
        r.fail(
            f"{ITEMS_REL} holds a JSON {type(parsed).__name__} and section 3.4.1 says its top "
            f"level is the items array itself, with nothing wrapping it"
        )
        return None

    r.ok(f"{ITEMS_REL} is a JSON array of {len(parsed)} entries, with nothing wrapping it")
    return parsed


def check_item_entries(r: Report, entries: list, where: str) -> None:
    """Section 3.4, applied to whichever shape the items arrived in.

    Both shapes carry the same array, so both are held to the same rules by the same code: an
    entry that would be wrong inline is not made right by being fetched from a second file.

    Every one of these is a *publisher's* check and none is a wallet's. A wallet ignores an
    entry whose `index` it does not hold and ignores members it does not recognize
    (`asset-metadata-v0.md` section 4.4), so each of these mistakes is silent downstream — a
    duplicate index is two entries where the second may or may not win depending on how a wallet
    walks the array, and an unrecognized member is detail the publisher wrote and nobody will
    ever see. Silent is the argument for catching them while they are still a local diff.
    """
    shape, bad_index, dupes, unknown, over_count, over_len = [], [], [], [], [], []
    seen: set[int] = set()

    for pos, entry in enumerate(entries):
        if not isinstance(entry, dict):
            shape.append(pos)
            continue
        idx = entry.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
            bad_index.append(pos)
            idx = f"at position {pos}"
        elif idx in seen:
            dupes.append(idx)
        else:
            seen.add(idx)

        for k in entry:
            if k not in ITEM_MEMBERS:
                unknown.append(f"{idx}.{k}")

        attrs = entry.get("attributes")
        if attrs is None:
            continue
        if not isinstance(attrs, list):
            shape.append(pos)
            continue
        if len(attrs) > ATTRS_MAX:
            over_count.append(f"{idx} has {len(attrs)}")
        for a in attrs:
            if not isinstance(a, dict) or not all(isinstance(a.get(m), str) for m in ("trait", "value")):
                shape.append(pos)
                continue
            for m in ("trait", "value"):
                if len(a[m]) > ATTR_STR_MAX:
                    over_len.append(f"{idx}.{m} is {len(a[m])}")

    r.check(not shape, f"{where}: every entry is an object, and every attribute a trait and a value, both strings  (malformed: {sorted(set(shape)) or 'none'})")
    r.check(not bad_index, f"{where}: every entry has an index that is a non-negative integer  (positions: {bad_index or 'none'})")
    r.check(not dupes, f"{where}: no index is carried twice  (repeated: {sorted(set(dupes)) or 'none'})")
    r.check(not unknown, f"{where}: every member is one section 3.4 recognizes, since a wallet drops the rest  (unrecognized: {unknown or 'none'})")
    r.check(not over_count, f"{where}: no entry carries more than {ATTRS_MAX} attributes  ({over_count or 'none'})")
    r.check(not over_len, f"{where}: every attribute string is {ATTR_STR_MAX} characters or fewer  ({over_len or 'none'})")


def verify(base: str) -> int:
    r = Report()

    pon = load("pon/c.json")
    table = pon.get("digest_table")
    items_doc = pon.get("items_document")

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
    if items_doc is not None:
        # And the items document, for the same reason and with the same consequence. It is named
        # by the collection document and pinned by the `b2` beside it, so no ASSET message ever
        # carries it either — but it is published next to the document and a publisher who
        # discovers the ceiling on one of them wants to discover it on both while the layout can
        # still be moved, rather than one uri at a time.
        budgeted["pon items"] = ITEMS_REL
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
    r.check(nc["schema"] == "nyctis-asset-metadata/1", f"schema {nc['schema']}")
    logo = ROOT / "nc/logo.png"
    r.check(nc["logo"]["b2"] == b2(logo), "logo.b2 matches nc/logo.png")
    r.check(logo.stat().st_size <= LOGO_MAX, f"logo {logo.stat().st_size} of {LOGO_MAX} bytes")
    r.check(nc["logo"]["uri"] == base + "nc/logo.png", f"logo.uri is under {base}")

    print("pon/c.json — spec/asset-collection-v0.md")
    pon_bytes = (ROOT / "pon/c.json").stat().st_size
    r.check(pon_bytes <= DOC_MAX, f"document {pon_bytes} of {DOC_MAX} bytes")
    r.check(pon["schema"] == "nyctis-collection-metadata/1", f"schema {pon['schema']}")
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
    cap = pon.get("max_supply")
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
            table.get("count") == cap,
            f"digest_table.count is {table.get('count')} for a declared max_supply of {cap}",
        )
    else:
        want = inline or []
        r.check(len(want) == cap, f"{len(want)} digests for a declared max_supply of {cap}")

    if want is None:
        # Section 3.3.1: a failure of the length or of `b2` leaves the whole collection
        # unpinned, not some of its members. Saying that once is the honest report; running the
        # loop below over nothing would print two reassuring lines about zero digests.
        r.fail("no digest can be read from the table, so every member is unpinned  (section 3.3.1)")
    else:
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

    # The same two-shape question, asked of per-piece detail instead of of digests. `items`
    # inline is what makes a document large — about 190 bytes a member, so a collection carrying
    # one attribute each runs out of the 16 KiB at 81 (section 3.4) — and `items_document`
    # (section 3.4.1) moves the array into a file the document pins by `b2` and sizes by `bytes`.
    # The document is then constant in `N`, which is the same property the table buys and for the
    # same reason: it is what lets every member of the collection sign one `uri`.
    #
    # The digest checks above can fail without reaching this, and must not skip it. An unreadable
    # table says nothing about whether the items are well formed, and a publisher fixing two
    # things wants to be told about both in one run.
    entries = pon.get("items")
    r.check(
        not (entries is not None and items_doc is not None),
        "the document carries items or items_document, not both  (section 3.4.1)",
    )
    if items_doc is not None:
        print(f"{ITEMS_REL} — section 3.4.1, the items document")
        entries = document_items(r, items_doc, base)
    if entries is not None:
        check_item_entries(r, entries, ITEMS_REL if items_doc is not None else "items")

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
        if doc.get("items_document") is not None:
            retarget_items(old, new)
    print()
    print("The documents' bytes changed, so every `#b2=` pin computed from the old bytes is stale.")
    print("Publish them, then mint: the pin is computed from the published document (nc/mint.sh).")
    return 0


def retarget_items(old: str, new: str) -> None:
    """Retarget the items document too, and re-pin it.

    It is not in `DOCS` and it has to be rewritten all the same, because an `items` entry may
    override `image` — so the file carries absolute URLs of its own, under the base that just
    moved. Rewriting them changes its length and its digest, and `items_document.bytes` and
    `.b2` are computed from exactly those bytes. A retarget that stopped at the document would
    leave it declaring the size and pinning the digest of the file it used to be, which is the
    one failure section 3.4.1 makes fatal: a wallet refuses a body of the wrong length.
    """
    path = ROOT / ITEMS_REL
    if not path.exists():
        print(
            f"{ITEMS_REL}: pointed at by pon/c.json and not here, so its b2 and bytes now pin a "
            f"file this cannot check. Rebuild it with `items --external` before publishing.",
            file=sys.stderr,
        )
        return
    blob = (path.read_text().replace(old, new)).encode()
    path.write_bytes(blob)
    doc = load("pon/c.json")
    doc["items_document"]["b2"] = b2_bytes(blob)
    doc["items_document"]["bytes"] = len(blob)
    dump("pon/c.json", doc)
    print(f"{ITEMS_REL}: {old} -> {new}, now {len(blob)} bytes, b2 {doc['items_document']['b2']}")


def items(external: bool) -> int:
    """Move pon/c.json's per-piece detail between the inline array and pon/i.json.

    Unlike `digests` there is nothing to recompute. `pon/art/` is the source of truth for a
    digest and there is no equivalent for a name or a trait, so the array the publisher wrote is
    the array either shape carries, and this only moves it. Nothing here touches its contents,
    which is what makes the two forms comparable: the items document is the bytes of the member
    that would otherwise be inline — the same array, indented the same way, at the top level
    with nothing wrapping it (`spec/asset-collection-v0.md` section 3.4.1).

    Whichever shape is written the other is removed, for the reason `digests` and `digest_table`
    are exclusive: two sources can then never disagree about one index, and a document shipping
    both is an error a wallet papers over rather than reports.
    """
    doc = load("pon/c.json")
    base = base_of(doc) or DEFAULT_BASE
    anchor = items_anchor(doc)
    spec = doc.get("items_document")
    have = doc.get("items")

    if have is not None and spec is not None:
        print(
            "pon/c.json carries both items and items_document, which section 3.4.1 forbids. "
            "Delete one and run this again — this cannot choose for you, because the two may "
            "differ and only the publisher knows which one is the intended detail.",
            file=sys.stderr,
        )
        return 2
    if spec is not None:
        path = ROOT / ITEMS_REL
        if not path.exists():
            print(f"pon/c.json points at {ITEMS_REL} and it is not here; there is nothing to move.", file=sys.stderr)
            return 2
        have = json.loads(path.read_text())
    if have is None:
        print(
            "pon/c.json carries no items. There is nothing to externalize: write the array "
            "inline first (section 3.4), then run this to move it out.",
            file=sys.stderr,
        )
        return 2

    if not external:
        doc = put_member(doc, ITEMS_PAIR, "items", have, anchor)
        dump("pon/c.json", doc)
        (ROOT / ITEMS_REL).unlink(missing_ok=True)
        size = (ROOT / "pon/c.json").stat().st_size
        print(f"pon/c.json: {len(have)} items moved inline, {size} of {DOC_MAX} bytes")
        if size > DOC_MAX:
            # Written, and then said to be too large, in that order and not the other way round:
            # the array is the publisher's and this is not the tool that decides to drop it. The
            # flush is so the two lines arrive after the one above them through a pipe.
            sys.stdout.flush()
            print(f"which is past the {DOC_MAX}-byte limit of asset-metadata-v0 section 3.2. That is what", file=sys.stderr)
            print("the items document is for: move them back out with `items --external`.", file=sys.stderr)
            return 1
        return 0

    blob = (json.dumps(have, indent=2, ensure_ascii=False) + "\n").encode()
    (ROOT / ITEMS_REL).write_bytes(blob)
    doc = put_member(
        doc,
        ITEMS_PAIR,
        "items_document",
        {"uri": base + ITEMS_REL, "b2": b2_bytes(blob), "bytes": len(blob)},
        anchor,
    )
    dump("pon/c.json", doc)
    print(f"{ITEMS_REL}: {len(blob)} bytes for {len(have)} items, b2 {doc['items_document']['b2']}")
    print(f"pon/c.json: items replaced by an items_document, {(ROOT / 'pon/c.json').stat().st_size} bytes")
    print()
    print("Publish the items document FIRST and the collection document second: the collection")
    print("document commits to its b2 AND to its exact length, so a document published against an")
    print("items document that is not up yet declares bytes nobody will be served — and a wallet")
    print("refuses a body whose length is not exactly `bytes`. pon/mint.sh checks that before it")
    print("signs anything.")
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
    size = doc["max_supply"]
    base = base_of(doc) or DEFAULT_BASE
    raw = [raw_b2(ROOT / f"pon/art/{i}.png") for i in range(size)]

    if not table:
        doc = put_member(doc, DIGEST_PAIR, "digests", [enc(d) for d in raw], "item")
        dump("pon/c.json", doc)
        (ROOT / TABLE_REL).unlink(missing_ok=True)
        print(f"pon/c.json: {size} digests recomputed from pon/art/, inline")
        return 0

    blob = b"".join(raw)
    (ROOT / TABLE_REL).write_bytes(blob)
    doc = put_member(
        doc,
        DIGEST_PAIR,
        "digest_table",
        {"uri": base + TABLE_REL, "b2": b2_bytes(blob), "count": size},
        "item",
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
# selftest — because the committed documents do not exercise either external form
#
# `pon/c.json` carries an inline `digests` array and no `items` at all, and both are right: a
# hundred members is well inside the 302 that `spec/asset-collection-v0.md` section 3.3 allows,
# and the collection's pieces differ only in their picture, which the `item` template already
# says. So the code that writes and reads a digest table, and the code that writes and reads an
# items document, is code this repository never runs in anger — and code nobody runs is code
# that works until the first person who needs it, who will be a publisher with ten thousand
# pieces and a naming message they can only send once.
#
# This builds each external form in a scratch copy, checks it says the same thing as the inline
# member it replaces, and then breaks it in the ways sections 3.3.1 and 3.4.1 name — a flipped
# byte, a length that is not the declared one, a duplicate index, both members at once — to
# check that `verify` says which, rather than reading past it.


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
        work = pathlib.Path(tmp) / "nyctis-assets"
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

            # ----- the items document, section 3.4.1 -----
            #
            # There is no committed `items` array to move the way there is a committed `digests`
            # array, so the selftest writes one: one attribute per member, which is the case
            # section 3.4 measures at about 190 bytes a member and a ceiling of 81. A hundred
            # members of it does not fit, and that is not incidental to the test — it is the
            # reason the member being tested exists, so it is asserted before anything else.
            print("one attribute per member does not fit inline")
            phases = ["dusk", "afterglow", "first dark", "moonrise",
                      "zenith", "drift", "the long hour", "greying"]
            synthetic = [
                {"index": i, "attributes": [{"trait": "phase", "value": phases[i % len(phases)]}]}
                for i in range(len(inline_before))
            ]
            doc = put_member(load("pon/c.json"), ITEMS_PAIR, "items", synthetic, items_anchor(load("pon/c.json")))
            dump("pon/c.json", doc)
            inline_size = (work / "pon/c.json").stat().st_size
            rc, out = _verify_quietly(base)
            t.check(
                rc != 0 and f"FAIL  document {inline_size} of {DOC_MAX} bytes" in out,
                f"{len(synthetic)} items inline is {inline_size} bytes, past the {DOC_MAX} of section 3.2",
            )

            print("the items document round-trips")
            items(external=True)
            rc, out = _verify_quietly(base)
            t.check(rc == 0, f"verify passes on the items document  ({out.count('  ok ')} checks)")

            doc = load("pon/c.json")
            body = (work / ITEMS_REL).read_bytes()
            out_size = (work / "pon/c.json").stat().st_size
            t.check("items" not in doc, "the inline items array is gone from the document")
            t.check(
                out_size <= DOC_MAX,
                f"and the document is {out_size} of {DOC_MAX} bytes again, constant in N whatever the items say",
            )
            t.check(
                doc["items_document"]["bytes"] == len(body),
                f"items_document.bytes is {doc['items_document']['bytes']}, the file's exact length",
            )
            t.check(
                doc["items_document"]["b2"] == b2_bytes(body),
                "items_document.b2 is the digest of the whole file",
            )
            t.check(
                json.loads(body.decode()) == synthetic,
                "and the file's top level is the items array itself, entry for entry, nothing wrapping it",
            )

            print("a length that is not the declared one is caught")
            # The right file, one byte longer than the document says. Section 3.4.1 has a wallet
            # refuse the body on the length alone — `bytes` exists so the cost is known before
            # the fetch, which is worth nothing if a body of another length is read anyway.
            doc["items_document"]["bytes"] = len(body) + 1
            dump("pon/c.json", doc)
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                f"FAIL  {ITEMS_REL} is {len(body)} bytes, and items_document.bytes declares {len(body) + 1}" in out,
                "and says so as a length, against the number the document declares",
            )
            t.check(
                "items_document.b2 matches" not in out,
                "and stops there, rather than hashing a body a wallet would have refused unread",
            )

            print("a flipped byte is caught")
            # Inside a trait name, so the file is still the same length and still parses. Only
            # `b2` can see this, which is why section 3.4.1 makes it required and not a SHOULD.
            doc["items_document"]["bytes"] = len(body)
            dump("pon/c.json", doc)
            broken = bytearray(body)
            broken[body.index(b"phase")] ^= 0x01
            (work / ITEMS_REL).write_bytes(bytes(broken))
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                f"FAIL  items_document.b2 matches {ITEMS_REL}" in out,
                "and says the file does not hash to what the document pins",
            )
            t.check(
                f"FAIL  {ITEMS_REL} is " not in out,
                "and not as a length, which is right: a swap that keeps the length is what b2 is for",
            )

            print("a duplicate index is caught")
            # Pinned and sized correctly, so nothing above this can see it. Two entries for one
            # member is two answers to `what is member 0`, and a wallet is not told to prefer
            # either — section 3.4 only says to ignore an index it does not hold.
            dupe = [dict(e) for e in synthetic]
            dupe[1]["index"] = dupe[0]["index"]
            raw = (json.dumps(dupe, indent=2, ensure_ascii=False) + "\n").encode()
            (work / ITEMS_REL).write_bytes(raw)
            doc["items_document"]["bytes"] = len(raw)
            doc["items_document"]["b2"] = b2_bytes(raw)
            dump("pon/c.json", doc)
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                f"FAIL  {ITEMS_REL}: no index is carried twice  (repeated: [0])" in out,
                "and names the index that appears twice",
            )

            print("an entry section 3.4 does not allow is caught")
            odd = [dict(e) for e in synthetic]
            odd[2] = {"index": 2, "immage": "typo", "attributes": [{"trait": "phase", "value": "x" * (ATTR_STR_MAX + 1)}]}
            raw = (json.dumps(odd, indent=2, ensure_ascii=False) + "\n").encode()
            (work / ITEMS_REL).write_bytes(raw)
            doc["items_document"]["bytes"] = len(raw)
            doc["items_document"]["b2"] = b2_bytes(raw)
            dump("pon/c.json", doc)
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                "unrecognized: ['2.immage']" in out,
                "and names the member a wallet would have dropped without a word",
            )
            t.check(
                f"every attribute string is {ATTR_STR_MAX} characters or fewer  (['2.value is {ATTR_STR_MAX + 1}']" in out,
                f"and the string that is over {ATTR_STR_MAX} characters",
            )

            print("carrying both members is caught")
            (work / ITEMS_REL).write_bytes(body)
            doc["items_document"]["bytes"] = len(body)
            doc["items_document"]["b2"] = b2_bytes(body)
            doc["items"] = synthetic
            dump("pon/c.json", doc)
            rc, out = _verify_quietly(base)
            t.check(rc != 0, "verify fails")
            t.check(
                "FAIL  the document carries items or items_document, not both" in out,
                "and says the two members are mutually exclusive",
            )

            print("and the items move back inline")
            doc = load("pon/c.json")
            del doc["items"]
            dump("pon/c.json", doc)
            t.check(
                items(external=False) == 1,
                f"moving all {len(synthetic)} back inline says it overruns the {DOC_MAX}-byte document",
            )
            t.check(
                load("pon/c.json")["items"] == synthetic,
                "and the array that comes back is the array that went out, entry for entry",
            )
            t.check(
                not (work / ITEMS_REL).exists(),
                f"and {ITEMS_REL} is gone, so no document can be read past to find it",
            )
            doc = load("pon/c.json")
            del doc["items"]
            dump("pon/c.json", doc)
            rc, out = _verify_quietly(base)
            t.check(rc == 0, "and without them the scratch copy is the shape this repository ships")
        finally:
            ROOT = home

    print()
    print("FAILED" if t.bad else "ok — each external form carries what the inline member carried.")
    print("Nothing outside the scratch copy was touched; the repository still ships `digests`")
    print("inline and no `items` at all.")
    return 1 if t.bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check every digest, every limit and the uri budget")
    v.add_argument("--base", default=None, help=f"base URL the documents are published under (default: read from the documents)")
    rt = sub.add_parser("retarget", help="point both documents at a different fork")
    rt.add_argument("--base", required=True, help="e.g. https://raw.githubusercontent.com/you/nyctis-assets/main/")
    dg = sub.add_parser("digests", help="recompute pon/c.json's digests from pon/art/")
    dg.add_argument(
        "--table",
        action="store_true",
        help=f"write them to {TABLE_REL} and carry a digest_table instead (section 3.3.1)",
    )
    it = sub.add_parser("items", help="move pon/c.json's per-piece detail in or out of the document")
    it.add_argument(
        "--external",
        action="store_true",
        help=f"write them to {ITEMS_REL} and carry an items_document instead (section 3.4.1)",
    )
    sub.add_parser("selftest", help="round-trip both external forms in a scratch copy; changes nothing here")
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
    if a.cmd == "items":
        return items(a.external)
    return digests(a.table)


if __name__ == "__main__":
    sys.exit(main())

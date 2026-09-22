# nightjar-assets

Two worked examples of putting an asset on a [Nightjar](https://github.com/micovi/nightjar)
channel: a fungible token and a collection of unique pieces. Each one is a directory holding
everything that example needs — its metadata document, its artwork, its note policies in Roost,
and a script that sends the messages.

| | | |
|---|---|---|
| [`nc/`](nc/) | **NightCash** | a fungible asset: 8 decimals, capped, `spec/asset-metadata-v0.md` |
| [`pon/`](pon/) | **Phases of One Night** | a hundred unique pieces sharing one document, `spec/asset-collection-v0.md` |

They are deliberately the same shape, because the point of putting them side by side is that a
unit of money and a unique piece of art are, in this protocol, almost the same object. Read
`nc/transfer.roost` and `pon/transfer.roost` together: identical policy, identical 34 bytes,
identical Poseidon377 root. Everything that distinguishes them is in `asset_id`.

## Run them

Each example ships a `mint.sh` that does the whole sequence, and takes `--dry-run`, which needs
no devnet, sends nothing, and prints the exact commands:

```sh
nc/mint.sh  --dry-run --base-url https://raw.githubusercontent.com/micovi/nightjar-assets/main/
pon/mint.sh --dry-run --base-url https://raw.githubusercontent.com/micovi/nightjar-assets/main/
```

Every command block in the three READMEs of this repository is `--dry-run` output. A command
block nobody has run is a guess, and these are marked where they are one.

`--base-url` is required and has no default. Point it at **your** fork: the pin the naming
message signs is computed from the document published at that URL, so a script that hard-coded
one GitHub account would be a worked example for nobody else. `tools/doc.py retarget --base <url>`
rewrites the absolute URLs inside the documents to match.

To actually send anything you need a Nightjar devnet (`infra/README.md` in the Nightjar
repository), `cargo build --release -p nightjar-cli`, and proving keys in `.devnet/keys`.

Check the documents against the files they point at, at any time, with no network:

```sh
tools/doc.py verify
```

Check the note policies — every `.roost` file here carries executable tests, and `check` runs
them. From the Nightjar repository root:

```sh
cargo run -p nightjar-lang --bin roost -- check ../nightjar-assets/nc/transfer.roost
```

## The rule that should govern your layout, and did not govern this one

**A path in a published document is a signed commitment, and moving it breaks the asset
permanently.**

An `ASSET` message (kind `0x04`) carries the `uri`, and step 4 of `spec/transition-v0.md` §9 is
one sentence: *if `Assets` already holds `asset_id`, ignore.* First valid message wins, that map
is never pruned, and there is no amendment message. So an asset is named **exactly once**, by
whoever made the public issuance, and the `uri` that naming carried is the `uri` for the life of
the channel. Move the file it points at and every wallet, for ever, fetches a 404 — with no
recovery, because re-naming is exactly what step 4 refuses.

Pinning makes it stricter rather than looser. `spec/asset-metadata-v0.md` §2.1 puts a `#b2=`
digest of the document in the signed `uri`, so the signature fixes the *bytes*, not just the
address. That is why `tools/lib.sh` computes the pin by fetching the **published** document
immediately before the naming message is signed, and fails rather than warning if it cannot:
naming against a document that is not up yet means being unpinned for good, or pinned to bytes
nobody will ever be served. `--allow-unpinned` exists, prints a warning that says *never*, and is
the honest way to take that decision on purpose.

**This repository was restructured anyway, and it is worth being exact about why that was
allowed.** Every path here moved: `nc.json` became `nc/a.json`, `pon/0.png` became
`pon/art/0.png`. The assets already named against the old paths on the current devnet are
therefore broken, and the answer is that they are being re-minted from scratch — a devnet
rebuild changes every `asset_id` in any case. **That is the only moment the rule is free: before
an asset has any users.** After it, the layout is not yours to change.

## What the layout is, and why the directories have short names

```
README.md              this
nc/                    worked example 1 — a fungible asset
  README.md            what NightCash is on the channel, and what is enforced where
  a.json               its asset-metadata-v0 document
  logo.png             the picture a.json pins with logo.b2
  transfer.roost       the policy a NightCash note carries: pk(owner)
  sale.roost           the policies that sell it: reservation (ZEC), offer (asset for asset)
  variations.roost     richer policies — none deployed, none built by the CLI
  mint.sh              issue, name, pay, sell, and a second wallet buying
pon/                   worked example 2 — a collection of unique pieces
  README.md            what a collection is on the channel, and why one shared document
  c.json               its asset-collection-v0 document, shared by every member
  logo.png             the collection's own picture
  art/0.png … 99.png   the member artwork, each pinned by digests[i]
  transfer.roost       the policy a piece carries: pk(owner) — the same policy
  sale.roost           the reservation that sells one piece
  variations.roost     royalties and issuer-gated resale — neither deployed
  mint.sh              two messages per member, one document for all of them
tools/
  lib.sh               devnet plumbing shared by both mint scripts
  doc.py               verify / retarget / regenerate digests
```

The example directories are called `nc` and `pon` rather than something readable, and that is
not terseness for its own sake. `uri_len ≤ 128` (`spec/transition-v0.md` §9), a `#b2=` pin costs
47 of those bytes, and what is left is **81 bytes for scheme, host and path**. The canonical base
here is 62 of them:

```
https://raw.githubusercontent.com/micovi/nightjar-assets/main/     62
                                                     nc/a.json     71  + 47 = 118 ✓
                                                    pon/c.json     72  + 47 = 119 ✓
                                     nc/metadata/asset.json        84  + 47 = 131 ✗
             examples/02-collection/metadata/collection.json      109  + 47 = 156 ✗
```

A conventional `examples/<long-name>/metadata/` tree does not fit, and a fork whose owner or
repository name is longer than `micovi/nightjar-assets` has less room than this. So the pinned
document sits two path segments deep with a one-letter filename, and everything that is *not*
in a signed `uri` — the artwork under `pon/art/`, the READMEs, the Roost sources, the scripts —
is named for what it is. `tools/lib.sh` refuses to mint if the arithmetic does not clear, which
is a better place to find out than after the naming message is signed.

## Nothing here is authoritative

An asset's identity is its `asset_id` on the channel, and its `name`, `symbol` and `decimals` are
signed on-chain in the `ASSET` message. A document in this repository is decoration a wallet may
show **after** the user has explicitly accepted that `asset_id` — never before, and never because
a wallet happens to hold the asset (`spec/asset-metadata-v0.md` §3.1, §5).

The same argument reaches collections, and it has to be made again rather than assumed, because a
collection is the level at which a name looks most like a guarantee. **`collection_id` is a
collection's identity**, exactly as `asset_id` is an asset's. It is derived from the issuer's key,
a collection label and the channel id, and hashed into every member's `asset_id`. The human name
in `pon/c.json` is not `collection_id` and is bound to nothing: two collections may share a name,
only one can share a `collection_id`. A wallet must show `collection_id` wherever it shows the
name.

None of this is a claim that the files here are trustworthy. It is the reason it does not matter
very much whether they are: a document can say anything, and the things that decide anything are
checked against the channel.

**Ids are perishable, and this is not a caveat but the normal case.** A channel is born from a
wallet's `uivk`, so every devnet rebuild produces a new `channel_id`; `collection_id` is derived
from the issuer's key, a label and that `channel_id`, and `asset_id` from `collection_id`, the
label, the index and the supply cap. So every rebuild changes every id. The two example READMEs
record what was deployed on the day they were written so that the repository can be checked
against the channel on the day it is read, not so the ids can be quoted later. If they do not
match what your node reports, the devnet has been rebuilt.

## Checking the repository against the channel

Nothing below needs this repository to be trusted; that is the point of running it.

```sh
nightjar assets --uivk <channel uivk> --keys .devnet/keys
curl -s http://127.0.0.1:8787/api/assets | jq '.items[] | {asset_id, name, symbol, index, collection_id, max_supply, uri}'
```

```sh
tools/doc.py verify
```

`verify` checks every artwork digest in `pon/c.json`, `logo.b2` in both documents, every size
limit of `spec/asset-metadata-v0.md` §3.2, and the uri budget above. As of this writing all 100
digests verify, `nc/a.json` is 898 bytes and `pon/c.json` is 6 047 bytes against the 16 KiB
document limit, and the hundred artworks total 279 110 bytes against the 512 KiB per-asset
ceiling — which is what makes §2.1's fetch-every-member option affordable here.

A digest that does not verify is worse than no digest at all: the spec requires a wallet to
discard a mismatching image without retrying, so the failure is silent and the artwork simply
disappears. Publishing one is a way of breaking a picture that no wallet will report.

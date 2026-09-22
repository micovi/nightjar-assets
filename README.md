# nightjar-assets

Two worked examples of putting an asset on a [Nightjar](https://github.com/micovi/nightjar)
channel: a fungible token and a collection of unique pieces. Each one is a directory holding
everything that example needs — its metadata document, its artwork, its note policies in Roost,
and a script that sends the messages.

| | | |
|---|---|---|
| [`nc/`](nc/) | **NightCash** | a fungible asset: 8 decimals, capped, `spec/asset-metadata-v0.md` |
| [`pon/`](pon/) | **Phases of One Night** | a hundred unique pieces sharing one document, `spec/asset-collection-v0.md` |

A unit of money and a unique piece of art are, in this protocol, almost the same object. Read
`nc/transfer.roost` and `pon/transfer.roost` together: identical policy, identical 34 bytes,
identical Poseidon377 root. Everything that distinguishes them is in `asset_id`.

Both sequences have been run end to end — issue, name, pay, sell, buy — against a Nightjar
channel on a **local Zcash test network** (regtest), which is not Zcash's public testnet and not
mainnet.

## Run them

Each example ships a `mint.sh` that does the whole sequence, and takes `--dry-run`, which needs
no node, sends nothing, and prints the exact commands:

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

To actually send anything you need a Nightjar node of your own on a local Zcash test network
(`infra/README.md` in the Nightjar repository), `cargo build --release -p nightjar-cli`, and
proving keys in `.devnet/keys`.

Check the documents against the files they point at, at any time, with no network:

```sh
tools/doc.py verify
```

Check the note policies — every `.roost` file here carries executable tests, and `check` runs
them. From the Nightjar repository root:

```sh
cargo run -p nightjar-lang --bin roost -- check ../nightjar-assets/nc/transfer.roost
```

## The layout, and why you only get to choose it once

**A path in a published document is a signed commitment.** An `ASSET` message (kind `0x04`)
carries the `uri`, and step 4 of `spec/transition-v0.md` §9 is one sentence: *if `Assets` already
holds `asset_id`, ignore.* First valid message wins, the map is never pruned, and there is no
amendment message — so an asset is named exactly once, and the `uri` that naming carried is its
`uri` for the life of the channel. Move the file it points at and every wallet fetches a 404, for
ever.

Pinning makes that stricter rather than looser: `spec/asset-metadata-v0.md` §2.1 puts a `#b2=`
digest of the document in the signed `uri`, so the signature fixes the *bytes* and not just the
address. `tools/lib.sh` therefore computes the pin from the **published** document immediately
before the naming message is signed, and fails rather than warns when it cannot — naming against
a document that is not up yet means being unpinned for good, or pinned to bytes nobody will be
served. `--allow-unpinned` takes that decision on purpose.

You may still move a layout while nothing has been named against it, or while the only assets
that have are on a network you are about to throw away, since rebuilding one changes every
`asset_id` in any case. That window is the whole exemption.

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
  lib.sh               node plumbing shared by both mint scripts
  doc.py               verify / retarget / regenerate digests
```

A signed `uri` has a byte budget, and it is worth knowing how to spend it before you choose a
layout. `uri_len ≤ 255` (`spec/transition-v0.md` §9) and a `#b2=` pin costs 47 of those bytes,
leaving **208 for scheme, host and path**. The canonical base here is 62 of them:

```
https://raw.githubusercontent.com/micovi/nightjar-assets/main/     62
                                                     nc/a.json     71  + 47 = 118
                                                    pon/c.json     72  + 47 = 119
             examples/02-collection/metadata/collection.json      109  + 47 = 156
```

255 is the ceiling of the wire format rather than a number somebody picked: `uri_len` is a `u8`,
so nothing larger can be expressed at all. A fork whose owner or repository name is much longer
than `micovi/nightjar-assets` has less room than the table above.

**A document too deep to carry its pin is a document that cannot be pinned.** It does not become
silently unpinned: without the pin, whoever controls the host can swap the document under an
asset whose issuer signed something else. `tools/lib.sh` refuses to mint when the arithmetic does
not clear — a better place to find out than after the naming message is signed, which is the last
chance there is.

Everything that is *not* in a signed `uri` — the artwork under `pon/art/`, the READMEs, the Roost
sources, the scripts — is named for what it is and carries no budget at all.

## What is signed, and what is only decoration

An asset's identity is its `asset_id` on the channel, and its `name`, `symbol` and `decimals` are
signed there too, in the `ASSET` message. A document in this repository is decoration a wallet may
show **after** the user has explicitly accepted that `asset_id` — never before, and never because
a wallet happens to hold the asset (`spec/asset-metadata-v0.md` §3.1, §5).

The same holds one level up, where a name looks most like a guarantee. **`collection_id` is a
collection's identity**, derived from the issuer's key, a collection label and the channel id, and
hashed into every member's `asset_id`. The human name in `pon/c.json` is bound to nothing: two
collections may share a name, only one can share a `collection_id`. A wallet must show
`collection_id` wherever it shows the name.

So none of these files has to be trustworthy. Everything that decides anything is checked against
the channel.

**No `channel_id`, `collection_id` or `asset_id` appears anywhere in this repository**, because
every one of them is perishable. A channel is born from a wallet's `uivk`, so each rebuild of the
network gives a new `channel_id`, a new `collection_id` derived under it, and a new `asset_id`
under that. Your deployment's ids will differ from everyone else's. Ask the channel instead — the
commands below are the only answer that is true on the day it is read.

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
limit of `spec/asset-metadata-v0.md` §3.2, and the uri budget above. All 100 digests verify,
`nc/a.json` is 921 bytes and `pon/c.json` is 6 070 bytes against the 16 KiB document limit, and
the hundred artworks total 279 110 bytes against the 512 KiB per-asset ceiling — which is what
makes §2.1's fetch-every-member option affordable here.

A digest that does not verify is worse than no digest at all: the spec requires a wallet to
discard a mismatching image without retrying, so the failure is silent and the artwork simply
disappears. Publishing one is a way of breaking a picture that no wallet will report.

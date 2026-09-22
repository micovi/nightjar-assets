# nightjar-assets

Metadata documents for the demonstration assets on the Nightjar **regtest devnet**. Two kinds of
document live here, governed by two specifications in the
[Nightjar repository](https://github.com/micovi/nightjar):

| file | kind | spec |
|---|---|---|
| `nc.json` | one asset's document | `spec/asset-metadata-v0.md` |
| `pon/c.json` | one **collection's** document, shared by all its members | `spec/asset-collection-v0.md` |

Everything else in the repository is an image one of those two points at.

## Nothing here is authoritative

An asset's identity is its `asset_id` on the channel, and its `name`, `symbol` and `decimals` are
signed on-chain in the `ASSET` message. A document in this repository is decoration a wallet may
show **after** the user has explicitly accepted that `asset_id` — never before, and never because a
wallet happens to hold the asset.

The same argument reaches collections, and it has to be made again rather than assumed, because a
collection is the level at which a name looks most like a guarantee. **`collection_id` is a
collection's identity**, exactly as `asset_id` is an asset's. It is derived from the issuer's key
and a collection label, and hashed into every member's `asset_id`. The human name in `pon/c.json`
is not `collection_id` and is bound to nothing: two collections may share a name, only one can
share a `collection_id`. A wallet must show `collection_id` wherever it shows the name.

None of this is a claim that the files here are trustworthy. It is the reason it does not matter
very much whether they are: a document can say anything, and the things that decide anything are
checked against the channel.

**The documents here are unpinned.** `spec/asset-metadata-v0.md` §2.1 lets an `ASSET` message pin
its document by carrying a `#b2=` digest in the signed `uri`; the messages on the current devnet
channel do not. So these documents are revocable by whoever controls `raw.githubusercontent.com`
and the repository, which is not the same set as whoever signed the assets. A wallet reading them
should say the metadata is unpinned wherever it says where the metadata came from.

## The two document kinds, and why the collection one is shared

`nc.json` is the ordinary case: one asset, one document, pointed at by that asset's `ASSET`
message.

`pon/c.json` is one document for a hundred pieces. An `ASSET` message names exactly one `asset_id`,
so a hundred-piece collection still needs a hundred `ASSET` messages — the shared document does not
change that. What it changes is that all hundred messages may carry **the same `uri` string**,
because the member's `index` is already on-chain and hashed into its `asset_id`. One document then
describes the whole collection, and a wallet holding forty pieces fetches it once.

That is not merely tidier, and the reason it is the *required* shape rather than a convenience is
privacy. A metadata fetch is a disclosure: it tells the host that someone is looking at that asset.
A hundred separate documents would mean up to a hundred requests whose *pattern* discloses which
pieces a wallet cares about — the collection equivalent of publishing a balance. **Prefer the shared
form; it is the private one** (`spec/asset-collection-v0.md` §2).

The shared document closes half the hole. `item.image` is still a per-piece URL, so a wallet that
fetches `pon/c.json` and then `pon/7.png` has disclosed the index one hop later on the same
connection. §2.1 of that spec is what closes the rest: once the user has accepted any member, a
wallet should fetch the artwork for **every** member, so that the set of images requested is a
function of the collection rather than of the wallet. Fetching is not displaying — a wallet must
still draw only what the user accepted.

Two things make the shared form as safe as a hundred separate documents would have been:

- **Substitution is fixed.** `item.name` and `item.image` contain the literal token `{index}` and
  nothing else is substituted. A wallet replaces it with the member's **on-chain** index. A
  document cannot lie about which piece is which: change the index and the `asset_id` no longer
  derives.
- **`digests` pins the artwork.** `digests[i]` is the BLAKE2b-256 of the image for index `i`, so
  one document fixes all hundred images, and whoever holds the host later cannot change a single
  one. A wallet that finds `digests[i]` must verify it and must discard a mismatching image without
  retrying. Every digest in this repository is verified in CI-less fashion by the command in
  [Checking this repository](#checking-this-repository-against-the-channel), and all 100 verify as
  of the date below.

## Phases of One Night: three numbers, all true

`pon/` is **Phases of One Night**, a demonstration collection. Each piece is its own public asset
with `max_supply = 1` and `decimals = 0` — indivisible and unique, and provably so to every
verifier, because the cap is hashed into the asset's identity rather than claimed in this
repository.

Three counts appear around this collection and they are easy to confuse, so:

| number | what it is | where it lives |
|---|---|---|
| **100** | how many pieces the publisher **intends** | `size` in `pon/c.json` |
| **100** | how many artworks and digests are **published** | `pon/0.png` … `pon/99.png`, `digests[0..99]` |
| **10** | how many members are **minted** on the devnet channel today | the channel, indices 0–9 |

`size` is advisory. The channel decides how many members exist, and `spec/asset-collection-v0.md`
§3.1 is explicit that a wallet **MUST NOT** treat a member whose index is ≥ `size` as invalid. So
`size` is a statement of intent, not a count and not a bound — which is exactly why 100 intended,
100 published and 10 minted can all be true at once, and why a wallet gets the member count from
the channel rather than from this file.

`size` stays at **100**, and the reason is `digests`. That array has no incremental form (§6, T3): a
collection that grows must republish the whole document, which changes its bytes and therefore any
`#b2=` that already-signed `ASSET` messages point at — so a growing collection either goes unpinned
or re-signs every member. The spec's only answer is to size `digests` for the final count at first
publication, which is what this document does. Cutting `size` to 10 would either leave 90 digests
for members the document says do not exist, which is incoherent, or drop them, which forfeits the
one mitigation T3 offers and makes minting #10 a re-signing event. Tracking the minted count would
also mean rewriting this document on every mint, which is precisely the churn the pinning story
cannot absorb. The honest fix for the mismatch that used to be here is not a smaller number; it is
saying which number means what.

The pieces are all 512×512 PNG and total 273 KiB, so §2.1's fetch-every-member costs about a
quarter of a megabyte for the whole collection — under the 512 KiB per-asset total that
`spec/asset-metadata-v0.md` §3.2 sets, which is why the leak-free option is affordable here.
`pon/c.json` is 5 818 bytes, against the 16 KiB document limit.

## What is deployed

Recorded **2026-09-22** against `channel_id`
`3d209fc7f081aef07b1aad9b2d1addfcac940afe6aab0c1dc98a8fd818e623f3`, `circuit_version 4`, network
`regtest`, at canonical height 832.

> **These ids are not durable and must not be treated as such.** A channel is born from a wallet's
> `uivk`, so every devnet rebuild produces a new `channel_id`. `collection_id` is derived from the
> issuer's key and a label, and `asset_id` from `collection_id`, the label, the index and the
> supply cap — so **every rebuild changes every id on this page**. They are recorded so the
> repository can be checked against the channel on the day it is read, not so they can be quoted
> later. If they do not match what your node reports, the devnet has been rebuilt; that is the
> normal case, not a fault.

### Assets with a document in this repository

| `asset_id` | name | symbol | dec | index | `max_supply` | document |
|---|---|---|---|---|---|---|
| `1c9339156f2a6402a51a423f95a4afcfb54335c8505b08bb5c3c970e47cdee05` | NightCash | NC | 8 | 0 | 10000000000000000 | `nc.json` |
| `85544f0793d86b23bebafbd382cc255a4815e512b3b8110721a80a1620eebd0f` | Phases of One Night #0 | PON | 0 | 0 | 1 | `pon/c.json` |
| `884c02bccca7d5349d90573b27987015abea06d57dc4b80f10ab70612859da01` | Phases of One Night #1 | PON | 0 | 1 | 1 | `pon/c.json` |
| `7b6bcea338b3cd48e57892049b72288fc98ddcdde6e333110f7d6c714e2c0c00` | Phases of One Night #2 | PON | 0 | 2 | 1 | `pon/c.json` |
| `f114349ee3b5d5054e99e17f4a1c2bccfa417e9d31edbdd1eeec24f12e00340e` | Phases of One Night #3 | PON | 0 | 3 | 1 | `pon/c.json` |
| `ee76f4ade2a79cae1f0ad661082d410163f31dadef5d7fe0c03d0538cfebb811` | Phases of One Night #4 | PON | 0 | 4 | 1 | `pon/c.json` |
| `112e167da6935691dae8bb21b57ffcf50e148e9051c269764c01210258700404` | Phases of One Night #5 | PON | 0 | 5 | 1 | `pon/c.json` |
| `9abad2a736aeba75ab8e09e83b412b602840ed0b57c95830c3338c613fa17f03` | Phases of One Night #6 | PON | 0 | 6 | 1 | `pon/c.json` |
| `d96a1518235237504fc30f8ba49949efeec00e29c072126e9546f683a8550c00` | Phases of One Night #7 | PON | 0 | 7 | 1 | `pon/c.json` |
| `0d0c434c003ae2915c8bffb4cda172eff17dae3c7444e46b4b59b177fbbec200` | Phases of One Night #8 | PON | 0 | 8 | 1 | `pon/c.json` |
| `03a5321feb32503f8e7a4922650cd1fa16888c6f3d769852e86d6d1eade0750a` | Phases of One Night #9 | PON | 0 | 9 | 1 | `pon/c.json` |

`collection_id` for NightCash is
`e4fe94e1bc2906f0112c05d9b8d914247801e1c062e2e4569dafecb92301e602`; every PON member above shares
`c3d9066bfe739238bdf9aa2b3b325eaf9bbd7e16e8a767aac9f0cac0697b5004`. Note that NightCash has a
`collection_id` too — every public asset does, and a singleton is simply a collection with one
member at index 0. It is not part of Phases of One Night, and nothing but the shared
`collection_id` groups those that are.

Each PON member's on-chain `name` is exactly what `item.name` substitutes to, which is a
coincidence worth being clear about: the `ASSET` message's `name` is signed and the template's
output is not, and a wallet renders the signed one. They agree here because the same tool wrote
both.

### Also on the channel, with no document here

- `86d7bc9a039aeaa46b2ce8315542e3b0b53ad0f8f070db2c874bd19f82f10c09` — **Devnet Mint** (`DMT`,
  decimals 2, `max_supply` 1000), whose `uri` is `https://example.invalid/dmt/`. It is the
  deliberate no-metadata case: a wallet must render the signed `name` and `symbol` and show no
  decoration, treating the unreachable document as absent rather than as an error.
- Two private assets, which have no `ASSET` message and therefore no `uri` and no document.

## Checking this repository against the channel

Nothing below needs this repository to be trusted; that is the point of running it.

The assets, from the Nightjar repository root:

```sh
nightjar assets --uivk <channel uivk> --keys .devnet/keys
```

or from the indexer, if one is running:

```sh
curl -s http://127.0.0.1:8787/api/assets | jq '.items[] | {asset_id, name, symbol, index, collection_id, max_supply, uri}'
```

Every artwork digest in `pon/c.json`, and `nc.json`'s `logo.b2` against `nc.png`:

```sh
python3 - <<'EOF'
import base64, hashlib, json
b2 = lambda p: base64.urlsafe_b64encode(
    hashlib.blake2b(open(p, 'rb').read(), digest_size=32).digest()).decode().rstrip('=')

meta = json.load(open('nc.json'))
print('nc.png', 'ok' if meta['logo']['b2'] == b2('nc.png') else 'MISMATCH')

c = json.load(open('pon/c.json'))
bad = [i for i, d in enumerate(c['digests']) if d != b2(f'pon/{i}.png')]
print(f"{len(c['digests'])} digests,", f"{len(bad)} mismatched", bad or '')
EOF
```

A digest that does not verify is worse than no digest at all: the spec requires a wallet to discard
a mismatching image without retrying, so the failure is silent and the artwork simply disappears.
Publishing one is a way of breaking a picture that no wallet will report.

## Layout

```
nc.json        NightCash asset document        spec/asset-metadata-v0.md
nc.png         its logo, 512×512 PNG, pinned by nc.json's logo.b2
pon/c.json     Phases of One Night collection document   spec/asset-collection-v0.md
pon/<i>.png    artwork for member i, 0 ≤ i ≤ 99, each pinned by digests[i]
```

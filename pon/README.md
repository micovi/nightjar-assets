# Phases of One Night — a collection, worked end to end

```
c.json             the collection document, shared by every member  (spec/asset-collection-v0.md)
logo.png           the collection's own picture, pinned by logo.b2
art/0.png … 99.png the member artwork, each pinned by digests[i]
transfer.roost     the policy a piece carries — the same policy a unit of money carries
sale.roost         the reservation that sells one piece
variations.roost   royalties and issuer-gated resale; neither deployed
mint.sh            two messages per member, one document for all of them
```

## What a collection is on the channel

**A Nightjar collection needs no new protocol**, and that sentence is the whole design rather
than a boast. A unique item is an ordinary public asset with `max_supply = 1`, and the cap is
hashed into the asset's identity, so *there is only one of these* is checked by every verifier
rather than claimed in a file. Members are grouped by `collection_id`, derived from the issuer's
key, a collection label and the channel id, and each carries an `index : u32`. Both are inputs to

```
asset_id = Poseidon_3(DS_ASSET, collection_id, label, terms)      terms carries index and max_supply
```

so a document **cannot lie about which piece is which**: change the index and the `asset_id` no
longer derives (`spec/asset-collection-v0.md` §1).

Each piece of Phases of One Night is therefore its own asset: `max_supply = 1`, `decimals = 0`,
`index = i`, all three hashed into `asset_id`, all three immutable. Indivisible and unique, and
provably so to everyone.

What is *not* on-chain: the collection's human name — `collection_id` is a hash of a label, not
the label — the artwork, and any per-piece description. Those are what `c.json` carries, and like
everything in `asset-metadata-v0.md` they are decoration that decides nothing.

## What is enforced, and by what

| | enforced by | consequence |
|---|---|---|
| "there is only one" | the circuit, via `asset_id` | `max_supply = 1` is in the identity. Not editable. Not forgeable. |
| "this is piece #7" | the circuit, via `asset_id` | `index` is in the identity, so the `{index}` substitution below cannot be redirected |
| membership of the collection | the circuit, via `collection_id` | unforgeable across issuers: `collection_id` commits to the issuer's key |
| who may spend a piece | that note's policy | `pk(owner)` |
| the piece's on-chain name | its own `ASSET` message, signed | one per member; there is no collection-level naming message |
| the collection's name, artwork, size | `c.json` | nothing |

`collection_id` is an unsalted hash of the issuer's key, the label and the channel id, so it
reveals the issuer to anyone who already holds that key *and* knows the channel, and links that
issuer's collections on one channel to anyone who can guess a label. It reveals nothing to anyone
else — which is why the issuer authority must be a dedicated key and not an address key
(`spec/note-format-v0.md` §8).

## The policy on a piece is the policy on a banknote

`transfer.roost` here and `../nc/transfer.roost` compile to the same thing:

```
pk(owner)                                  34 bytes    root 4e076ed535abceb4
```

A unique piece of art and a unit of money carry the identical note policy. Everything that makes
one an NFT is in `asset_id`. There is no clause here saying "this cannot be split", because it
would be dead code: the circuit's conservation rule cannot produce two units of an asset whose cap
is one, whatever a policy says.

The two things people expect an NFT standard to provide — an enforced royalty, and a transfer the
issuer can gate — are both writable and both in `variations.roost`, **neither deployed and neither
built by any CLI command**. Read the `cancel` clause of the royalty listing before believing the
royalty is enforced: delisting takes the piece out of that policy and into a plain `pk(owner)`
note, after which the next sale pays nobody. **A royalty binds a listing, not an asset**, and no
note policy in this protocol can do otherwise — a policy governs the note it is on, and the seller
chooses the policy of the note they create next.

## The sale policy, and what a collection cannot use

Selling a piece is `programs::reservation_policy` (`crates/nightjar-cli/src/programs.rs:223`) with
`amount 1` — the identical four lines that sell a lot of NightCash. There is no NFT-specific sale
mechanism and `sale.roost` is not hiding one; `../nc/README.md` documents F5, F14 and F16 at
length and all of it applies unchanged.

**There is no partial-fill form, and that is not an omission.** `programs::offer_policy` splits a
lot: the taker takes some and the remainder stays on offer under the same policy. A lot of one
cannot be split, so a piece has two states — reserved or not — and the whole remainder machinery
has nothing to do.

**Minting is still not a policy.** A collection that lets the public mint would have to sell
*pre-minted* pieces exactly as `mint.sh` does: the issuer mints first and then puts each one up,
because there is nowhere in a note policy to write "whoever pays may create a new asset".

## One document for a hundred pieces

An `ASSET` message names exactly one `asset_id`, so a hundred-piece collection still needs a
hundred `ASSET` messages. The shared document does not change that and `spec/asset-collection-v0.md`
§6 T1 says so plainly: this removes the hundred *documents* and the hundred *fetches*, not the
hundred *messages*. The protocol change that would remove them is unimplemented.

What it changes is that all hundred messages may carry **the same `uri` string**, because the
member's `index` is already on-chain and hashed into its `asset_id`. One document describes the
whole collection, and a wallet holding forty pieces fetches it once. `mint.sh` computes the pin
**once, before the loop**, for that reason — and also because a hundred separate fetches would be
a hundred chances to catch the host mid-edit and sign a hundred different digests for one file.

**It is not merely tidier; it is the private form** (§2). A metadata fetch is a disclosure: it
tells the host that someone is looking at that asset. A hundred separate documents would mean up
to a hundred requests whose *pattern* discloses which pieces a wallet cares about — the collection
equivalent of publishing a balance.

### §2.1 corrects §2, and the correction is the part to read

Revision 1 of that spec claimed the shared document discloses "nothing about which members", and
revision 2 added §2.1 to say that was false. **The document is shared; the images are not.**
`item.image` is a per-piece URL, so a wallet that fetches `c.json` and then `art/7.png` has
disclosed the index one hop later on the same connection.

It matters because of what the fetched set correlates with. The image fetch is gated on
acceptance, acceptance is a per-`asset_id` decision, and a user accepts the pieces they care
about — which for a collection is overwhelmingly the pieces they hold. §5's "**MUST NOT** fetch a
collection document because it *holds* a member" is then obeyed to the letter and defeated in
effect.

The shared form is still better: it takes the index out of the document URL and halves the number
of index-revealing requests. But the improvement is a **constant factor**, not the qualitative
claim §2 made. Closing the rest means making the set of images requested a function of the
*collection* rather than of the wallet, so §2.1 asks a wallet, once the user has accepted any
member, to fetch the artwork for **every** member — including ones it does not hold. Fetching is
not displaying: a wallet must still draw only what the user accepted.

That option is affordable here and the numbers are why. All hundred artworks are 512×512 PNG and
total **279 110 bytes**, against the 512 KiB per-asset ceiling of `spec/asset-metadata-v0.md`
§3.2. A collection with larger images pushes a wallet into §6 T5's residue: cap the transfer and
the tail leaks, or do not cap it and no phone wallet should make that transfer silently.

### Two things make the shared form as safe as a hundred separate ones

- **Substitution is fixed.** `item.name` and `item.image` contain the literal token `{index}` and
  nothing else is substituted (§3.2). A wallet replaces it with the member's **on-chain** index. A
  document cannot lie about which piece is which.
- **`digests` pins the artwork.** `digests[i]` is the BLAKE2b-256 of the image for index `i`, so
  one document fixes all hundred images and whoever holds the host later cannot change one. A
  wallet that finds `digests[i]` **must** verify it and **must** discard a mismatching image
  without retrying — which is why a wrong digest is worse than none: the failure is silent.
  `tools/doc.py verify` checks all hundred.

## Three numbers, all true

| number | what it is | where it lives |
|---|---|---|
| **100** | how many pieces the publisher **intends** | `size` in `c.json` |
| **100** | how many artworks and digests are **published** | `art/0.png` … `art/99.png`, `digests[0..99]` |
| **grows** | how many members are **minted** so far | the channel — ask it, do not read it here |

`size` is advisory. The channel decides how many members exist, and §3.1 is explicit that a wallet
**MUST NOT** treat a member whose index is ≥ `size` as invalid. So `size` is a statement of
intent, not a count and not a bound.

**The third number is deliberately not written down here, and that is the point.** A collection is
launched, not completed: the publisher declares how many pieces there will be and puts the artwork
up for all of them, and the pieces are minted over time. A collection exists on the channel from
its first member, because `collection_id` derives from the issuer's key and the label rather than
from any count. Writing today's minted total into this file would make it wrong on the next mint,
and would quietly suggest the collection is finished when the opposite is true.

`size` stays at 100, and the reason is `digests`. That array has no incremental form (§6 T3): a
collection that grows must republish the whole document, which changes its bytes and therefore any
`#b2=` that already-signed `ASSET` messages point at — so a growing collection either goes
unpinned or re-signs every member, and re-signing is what §9 step 4 of `transition-v0.md` refuses.
The spec's only answer is to size `digests` for the final count at first publication, which is
what this document does. Cutting `size` to 10 would either leave 90 digests for members the
document says do not exist, or drop them and forfeit the one mitigation T3 offers.

## What gets sent

`mint.sh --dry-run --count 5` prints the whole sequence and sends nothing. Per member:

```
TRANSITION  issue   --public --max-supply 1 --collection "Phases of One Night" --index i
ASSET       name    "Phases of One Night #i", symbol PON, decimals 0, the SAME uri every time
```

then once, for the whole run:

```
TRANSITION  sell    a reservation for one piece the issuer still holds
NOTE        publish
TRANSITION  buy     a second wallet pays 2 ZEC and takes it
```

`--index` is the loop variable and never a constant. Two pieces sharing an index would be two
distinct assets resolving to one picture, both claiming to be pinned; the CLI refuses a unique
item in a `--collection` with no `--index` for that reason.

**Minting a piece and giving it away are different things.** A collection's point is that *which*
pieces exist is public while *which* you hold is private, so an issuer who pays every piece to one
recipient has demonstrated nothing. `mint.sh` pays the first three away, sells one, and leaves the
rest with the issuer.

## What is deployed, and why no id is written down here

Nothing in this file records a `collection_id`, an `asset_id`, a height, or how many members
exist. `collection_id` is derived from the issuer's key, the collection label and the
`channel_id`, and the `channel_id` from a wallet's `uivk` — so the same commands run against your
node give different ids from anyone else's, and an id copied into a README is a claim about one
deployment at one moment. The member count is worse still: it is not fixed even on one channel,
because a collection is launched rather than completed. Ask the channel:

```sh
./target/release/nightjar assets --uivk <channel uivk> --keys .devnet/keys
curl -s http://127.0.0.1:8787/api/assets | jq '.items[] | select(.symbol=="PON")'
```

Wrap that filter in an array and pipe it to `length` and you have the member count, which is
the only place the count is true: not `size` in `c.json`, and not a paragraph here.

What is worth reading in that report is each member's `uri`, and there are two ways it can be
dead. Named with a bare `uri` — no `#b2=` — a member is permanently unpinned: its document is
revocable by whoever controls the host, which is not the set of parties who signed the asset, and
`ASSET` is first-valid-wins (`spec/transition-v0.md` §9 step 4), so no re-naming can fix it.
Named with a pin, the member is fixed to the document's bytes as they stood when the message was
signed — so republishing `c.json` with different bytes means a conforming wallet fetches it, finds
the digest does not match, discards it without retrying, and shows the collection with no metadata
at all.

That is what §2.1's correction and §6 T3 look like when they actually bite. A pinned document is
safer than an unpinned one and **harder to change**, and the two properties are the same property.

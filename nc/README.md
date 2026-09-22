# NightCash — a fungible asset, worked end to end

```
a.json             the metadata document  (spec/asset-metadata-v0.md)
logo.png           the picture a.json pins with logo.b2
transfer.roost     the policy a NightCash note carries
sale.roost         the policies that sell it
variations.roost   policies nothing here deploys
mint.sh            the messages, in order
```

## What NightCash is on the channel

Three integers and a key, hashed:

```
collection_id = Poseidon_3(DS_COLLECTION, enc(issuer_ak), collection_label, channel_id)
terms         = Fq(visibility + 2·max_supply + 2^65·index)
asset_id      = Poseidon_3(DS_ASSET, collection_id, label, terms)
```

(`spec/note-format-v0.md` §8.) For NightCash: `visibility = 1`, `max_supply = 10^16` — that is
100,000,000 display units at 8 decimals — `index = 0`, `collection_label = 0`, which is the
issuer's default collection. Nothing else. There is no asset contract, no registry entry and no
deploy step; the asset exists the moment a transition proves knowledge of those inputs and
publishes the resulting `asset_id`.

Note that NightCash **has a `collection_id`**. Every asset does: `collection_label = 0` is the
issuer's default collection, so every asset belongs to exactly one and an indexer can always
group. A standalone asset is a collection with one member at index 0. That is why `pon/` needs no
new protocol to be a collection — see its README.

## What is enforced, and by what

This is the division worth getting right, because the second column is the one people assume is
bigger than it is.

| | enforced by | consequence |
|---|---|---|
| the supply cap | the circuit, via `asset_id` | `max_supply` is hashed into the identity. An issuer who wants another cap gets **another asset**. Not editable, not upgradable. |
| the issued supply | the verifier's `Issued` map | every public issuance adds to a public running total, and the cap bounds it (`spec/transition-v0.md` §6c, §7) |
| one asset cannot become another | the circuit's conservation rule | nothing in a note policy needs to say so |
| who may mint | the issuer's key | a spend-authorization signature under a randomized `issuer_ak`, inside the proof |
| who may spend a note | that note's policy | `pk(owner)`, and that is all — see below |
| `name`, `symbol`, `decimals` | the `ASSET` message, signed | on-chain, and **declared**, never verified: two assets may carry one name |
| the logo, the description, the links | `a.json` | nothing. Decoration, shown only after the user accepts the `asset_id`. |

`decimals ≤ 8` is a hard rule rather than a convention (`spec/transition-v0.md` §9): `amount` is
a `u64` everywhere in this protocol, so 8 decimals leave about 1.8 × 10^11 whole units, where 18
would leave about 18 in total.

**What a cap does not do is bound an issuer.** It caps an `asset_id`. The same issuer may mint a
twin — same name, same decimals, same document — under a different label or a different key, with
its own cap. The twin is *detectable*, which is the whole difference: it has a different
`asset_id`, so a holder who keys on `asset_id` holds what they think they hold
(`spec/note-format-v0.md` §8).

## The transfer policy: `pk(owner)`, and nothing else

`wallet issue` and `wallet pay` both build the output note with
`account.address().default_policy()` (`crates/nightjar-cli/src/main.rs:558`, `:610`), which is
one line: `Policy::Key(self.ak)` (`crates/nightjar-zk/src/keys.rs:230`).

That is the whole policy of a NightCash note. 34 bytes. One leaf. **A fungible asset needs no
clever policy**, because the asset semantics live in `terms` and in the circuit's conservation
rule, not in the note. `transfer.roost` is that policy with its checks, plus the one variant the
CLI also builds: `wallet pay --expires h` makes the recipient's note `pk(recipient) && before(h)`,
which publishes `h` in the clear and, past it, freezes the note for the recipient too — a
deadline, not an escrow.

An example that put an elaborate policy on a held note to look impressive would be teaching the
opposite of what is true. `variations.roost` shows what a policy *can* buy — a second key that can
recover a balance after a height, an allowance that pays out no faster than a rate — and every
note in it is clearly marked: **nothing in that file is deployed and nothing in it is built by any
CLI command.**

## The sale policy: how anyone else comes to hold NC

There is no "mint policy". Minting is a key the issuer holds, not a clause anybody can read off a
note, and there is nowhere in a policy to write it down. What a policy governs is the note the
issuer puts **up for sale**, and `sale.roost` has the two the shipped CLI builds.

**Reservation** — `programs::reservation_policy` (`crates/nightjar-cli/src/programs.rs:223`),
driven by `wallet sell` and `wallet buy`:

```
pk(S) && after(h_exp) || (pk(B) && zec(H_order, price))
```

A named buyer takes the lot by paying ZEC in the same Zcash transaction that carries the
transition. Both halves were missing from the first shipped `sell` and each absence lost money
(F5). `pk(B)` names the buyer, because ZEC never enters Nightjar and this protocol cannot refuse a
Zcash payment — on an openly fillable order two buyers filling in one block both pay while only
the lower `(height, tx_index)` is applied, and the loser forfeits the price, not a fee.
`after(h_exp)` time-locks the seller's cancel, because a channel's viewing key is published and an
untimelocked `pk(S)` lets the seller read the payment in the mempool and self-spend at a higher
fee, keeping the lot *and* the ZEC. `wallet sell` requires `--buyer` for the first reason and
defaults `--expires-in` for the second.

`S` and the ZEC account are a **per-order** identity, not the seller's account.
`programs::check_no_account_identity` refuses to publish an order carrying either: a `NOTE`
message broadcasts the policy verbatim, so an account-level `pk()` leaf publishes two thirds of
the maker's address (F14) and an account-level `zec()` leaf publishes the incoming viewing key to
that wallet's whole Zcash history, past and future (F16).

**Offer** — `programs::offer_policy` (`:282`), driven by `wallet offer` and `wallet fill`: a
partially fillable lot, asset for asset, with the payout and the unsold remainder both
`recoverable`. Two fields in it are pinned rather than left open, and each was a bug first:

- the payout's `data` is pinned to `0` (F3). `recoverable` lets the maker rebuild the note from
  public data with no ciphertext, but recovery needs *every* commitment field determined. With
  `data` open, a taker who paid the exact price could set it to a random field element — covenant
  satisfied, taker no worse off, maker searching `2^253` values for their own money.
- the remainder is `amount == self - out 0`, pinned and `recoverable` (N3). Unsold inventory is
  not a courtesy the taker may withhold. It used to be `amount < self` with `data` open and no
  flag, so the goods that did not sell came back only if the taker volunteered a `NOTE`
  publication that nothing compels and whose withholding is economically identical to an honest
  fill.

`sale.roost` compiles both and tests them, including the failures: paying one zatoshi short,
paying with the wrong key, cancelling too early, filling without leaving the remainder.

## What gets sent, and why the order is forced

`mint.sh --dry-run` prints this and sends nothing:

```
1. TRANSITION  issue     a public issuance; creates the asset and its first units
2. ASSET       name      name, symbol, decimals, the pinned uri, signed by the issuer
3. TRANSITION  pay       units to a holder who is not the issuer
4. TRANSITION  sell      a reservation note: a lot for ZEC, for one named buyer
5. NOTE        publish   puts the reservation in the order book
6. TRANSITION  buy       the buyer pays ZEC in the carrying transaction and takes the lot
```

Three places the order is not a preference:

- **`name` after `issue`, necessarily.** The `ASSET` message carries `issuance_msg_id`, and a
  verifier checks it names a kind-`0x01` message it has **applied** whose `issue_public` was 1 and
  whose asset matches (`spec/transition-v0.md` §9 step 2). There is no naming an asset that does
  not exist. The lookup is in `Public issuances`, which is retained for the life of the channel,
  not in `Applied`, which the anchor window prunes — so a naming may legitimately arrive years
  after its issuance.
- **the document is published before the naming message is signed.** The `#b2=` pin is computed
  from the published bytes, and `ASSET` is first-valid-wins (§9 step 4): the first naming is the
  only one. `tools/lib.sh`'s `pin()` fetches and fails rather than warning, for that reason.
- **enough blocks between minting a note and spending it.** A note is not selectable until it is
  `FINALITY_DEPTH` blocks deep (`spec/transition-v0.md` §7); the node reports 10 and the script
  waits 12. Skip it and you get `insufficient funds: 0 ... available` several steps from its
  cause.

## What `a.json` contributes

A logo, a description, a website and two links. Nothing else, and nothing that decides anything.

`logo.b2` is the BLAKE2b-256 of `logo.png`, base64url unpadded, so one document fixes the picture
and whoever holds the host later cannot change it (`spec/asset-metadata-v0.md` §4.2). A wallet
that finds a `b2` **must** verify it and **must** discard a mismatch without retrying. The
document itself is fixed the same way, one level up, by the `#b2=` in the signed `uri`.

A wallet must **not** fetch this document because it holds a NightCash note — holding is the fact
the system exists to hide, and a fetch tells the host that someone at that address is looking at
that asset (§3.1). Acceptance is the trigger, and acceptance is a disclosure the user chose.

## What is deployed, and why no id is written down here

Nothing in this file records an `asset_id`, a `collection_id`, a `channel_id` or a height. That is
the honest form rather than an omission: a channel is born from a wallet's `uivk` and every id
below it derives from that, so the same sequence run against your node produces ids that are not
the ones anybody else's run produced. An id in a README is a claim about one deployment at one
moment, and a reader of it has neither. Ask the channel you ran this against:

```sh
./target/release/nightjar assets --uivk <channel uivk> --keys .devnet/keys
curl -s http://127.0.0.1:8787/api/assets | jq '.items[] | select(.symbol=="NC")'
```

Two fields in that report repay reading closely, because neither is fixable afterwards.
`max_supply` is hashed into `asset_id`, so it is the cap this asset will ever have — a different
cap is a different asset. And the `uri` is the one signed for good: `ASSET` is first-valid-wins
(§9 step 4), so an asset named with a bare `uri` can never be re-named with a pinned one, and one
named against a path that later moves resolves to nothing for the life of the channel. A wallet
reading an unpinned `uri` should say the metadata is unpinned wherever it says where the metadata
came from.

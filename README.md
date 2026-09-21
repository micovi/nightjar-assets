# nightjar-assets

Asset metadata documents for Nightjar demonstration assets on the regtest devnet.

Each `<symbol>.json` follows [`spec/asset-metadata-v0.md`](https://github.com/micovi/nightjar)
in the Nightjar repository: the document a public asset's `ASSET` message points at, carrying a
logo, a description and links.

Nothing here is authoritative. An asset's identity is its `asset_id` on the channel, and its
`name`, `symbol` and `decimals` are signed on-chain in the `ASSET` message. A document in this
repository is decoration a wallet may show **after** the user has explicitly accepted that
`asset_id` — never before, and never because a wallet happens to hold the asset.

## Collections

`pon/` is **Phases of One Night**, a six-piece demonstration NFT collection. Each piece is its own
asset with `max_supply = 1` and `decimals = 0` — indivisible, and provably so to every verifier,
because the cap is part of the asset's identity rather than a claim in this repository.

The pieces are grouped on-chain by `collection_id`, which is derived from the issuer's key and the
collection label. Their `index` (0-5) is likewise part of each `asset_id`. Neither is taken from
these files: a document here can say anything, and the grouping is checked against the channel.

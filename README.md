# nightjar-assets

Asset metadata documents for Nightjar demonstration assets on the regtest devnet.

Each `<symbol>.json` follows [`spec/asset-metadata-v0.md`](https://github.com/micovi/nightjar)
in the Nightjar repository: the document a public asset's `ASSET` message points at, carrying a
logo, a description and links.

Nothing here is authoritative. An asset's identity is its `asset_id` on the channel, and its
`name`, `symbol` and `decimals` are signed on-chain in the `ASSET` message. A document in this
repository is decoration a wallet may show **after** the user has explicitly accepted that
`asset_id` — never before, and never because a wallet happens to hold the asset.

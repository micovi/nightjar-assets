#!/usr/bin/env bash
# NightCash — the messages that have to be sent, in the order they have to be sent in.
#
#   nc/mint.sh --dry-run --base-url https://raw.githubusercontent.com/you/nightjar-assets/main/
#   nc/mint.sh           --base-url https://raw.githubusercontent.com/you/nightjar-assets/main/
#
# Run it with `--dry-run` first. It needs no node, sends nothing, and prints the exact
# sequence; that output is what the README's command blocks are.
#
# Five messages, and the order of the first two is forced by the protocol while the order of the
# rest is forced by finality:
#
#   1. TRANSITION  issue     a public issuance: creates the asset and its first units
#   2. ASSET       name      name, symbol, decimals and the pinned uri, signed by the issuer
#   3. TRANSITION  pay       units to a holder who is not the issuer
#   4. TRANSITION  sell      a reservation note: a lot for ZEC, for one named buyer
#   5. NOTE        publish   puts the reservation in the order book
#   6. TRANSITION  buy       the BUYER pays ZEC in the carrying transaction and takes the lot
#
# Why the order is what it is, in the three places it is not obvious:
#
#   * `name` after `issue`, necessarily. The `ASSET` message carries `issuance_msg_id` and a
#     verifier checks it names a kind-0x01 message it has **applied** whose `issue_public` was 1
#     and whose asset matches (`spec/transition-v0.md` section 9 step 2). There is no naming an
#     asset that does not exist yet.
#   * the document is **published before** the naming message is signed, because the `#b2=` pin
#     is computed from the published bytes. `ASSET` is first-valid-wins (section 9 step 4), so
#     the first naming is the only one: an asset named against an unpublished document is either
#     unpinned for good or pinned to bytes that are not what a wallet will fetch.
#   * `bury` between every mint and the spend of what it minted. A note is not selectable until
#     it is FINALITY_DEPTH blocks deep (`spec/transition-v0.md` section 7), and skipping this is
#     how you get `insufficient funds: 0 ... available` several steps from its cause.
set -euo pipefail
cd "$(dirname "$0")/.."
. tools/lib.sh

usage() {
  echo "usage: nc/mint.sh --base-url URL [options]"
  usage_common
}
parse_args "$@"

# `--amount` and `--max-supply` are in the asset's smallest unit. `max_supply` is part of the
# asset's **identity** — `terms = Fq(visibility + 2*max_supply + 2^65*index)` is hashed into
# `asset_id` (`spec/note-format-v0.md` section 8) — so it cannot be changed later without
# producing a different asset. An issuer who wants another cap gets another asset.
DECIMALS=8
UNIT=$(( 10 ** DECIMALS ))
CAP=$(( 100000000 * UNIT ))   # 100,000,000 NC, the number hashed into asset_id
MINT=$((   1000000 * UNIT ))  # what this run issues
PAY=$((       1000 * UNIT ))  # what it pays to the recipient
LOT=$((       5000 * UNIT ))  # what it puts up for sale
PRICE=50000000                # 0.5 ZEC in zatoshi

DOC="${BASE_URL}nc/a.json"

init_wallets

say "funding the issuer and the buyer from the treasury"
# Six messages from the issuer, each needing its own unconfirmed-change-free note; the buyer
# needs whole ZEC rather than fee money, because the price is a real payment the verifier checks
# on the carrying Zcash transaction.
fund "$ISSUER" 8 480000
fund "$BUYER"  3 80000000

say "1. issue — a public issuance creates the asset"
read -r ASSET ISSUANCE <<<"$(issue_public NIGHTCASH "$MINT" "$CAP" "" 0)"
bury
echo "   asset_id $ASSET"

say "2. name — the ASSET message, with the document pinned"
# Computed here, from the bytes actually served at that URL, and not from the working copy.
URI=$(pin "$DOC")
echo "   uri $URI"
uri_budget "$DOC"
name_asset "$ASSET" "$ISSUANCE" "NightCash" "NC" "$DECIMALS" "$URI"
bury

say "3. pay — units to somebody who is not the issuer"
pay_asset "$ASSET" "$PAY" "$RECIPIENT"
bury

if [ "$NO_SALE" = 0 ]; then
  sell_and_buy "$ASSET" "$LOT" "$PRICE" "NightCash"
fi

say "done — what the channel says about it now"
echo "   $NJ_BIN assets --uivk <channel uivk> --keys $KEYS"
echo "   curl -s http://127.0.0.1:8787/api/assets | jq '.items[] | select(.symbol==\"NC\")'"
echo
echo "   asset_id $ASSET"
echo "   Nothing in this repository is what makes that id NightCash. Ask the channel."

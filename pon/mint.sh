#!/usr/bin/env bash
# Phases of One Night — the messages that have to be sent, in the order they have to be sent in.
#
#   pon/mint.sh --dry-run --base-url https://raw.githubusercontent.com/you/nightjar-assets/main/
#   pon/mint.sh --count 10 --base-url https://raw.githubusercontent.com/you/nightjar-assets/main/
#   pon/mint.sh --from 10 --count 90 --no-sale --base-url ...      # resume, mint only
#
# Run it with --dry-run first. It needs no devnet and sends nothing.
#
# **Two messages per member, and one document for all of them.** That is the whole shape of a
# Nightjar collection:
#
#   per piece   TRANSITION  issue   --collection <label> --index <i> --max-supply 1
#   per piece   ASSET       name    "Phases of One Night #<i>", the SAME uri every time
#   once        TRANSITION  sell    a reservation for one piece
#   once        NOTE        publish
#   once        TRANSITION  buy     a second wallet pays ZEC and takes it
#
# The uri is computed **once**, before the loop. Every member's ASSET message signs the same
# string, which is what spec/asset-collection-v0.md section 2 is about: the member's index is
# already on-chain and hashed into its asset_id, so a document can be shared without being able
# to lie about which piece is which. Ten separate pins would also be ten chances to catch the
# host mid-edit and sign ten different digests for one file.
#
# **What this does not remove is the hundred messages.** An ASSET message names exactly one
# asset_id, so a hundred-piece collection is a hundred issuances and a hundred namings, each with
# its own proof. spec/asset-collection-v0.md section 6 T1 says so plainly and names the
# unimplemented protocol change that would fix it. This script removes the hundred *documents*
# and the hundred *fetches*, and nothing else.
#
# **Minting a piece and giving it away are different things.** A collection's point is that
# *which* pieces exist is public while *which* you hold is private, so an issuer who pays every
# piece to one recipient has demonstrated nothing. This mints --count pieces, pays the first
# three away, and sells exactly one of the rest — so the split is visible on the channel.
set -euo pipefail
cd "$(dirname "$0")/.."
. tools/lib.sh

usage() {
  echo "usage: pon/mint.sh --base-url URL [--from N] [--count N] [options]"
  usage_common
  echo "  --from N           first piece index to mint (default 0)"
  echo "  --count N          how many pieces to mint (default 10)"
}
parse_args "$@"

COLLECTION="${PON_COLLECTION:-Phases of One Night}"
DOC="${BASE_URL}pon/c.json"
PAY_FIRST=3      # how many of the minted pieces to pay away
PRICE=200000000  # 2 ZEC in zatoshi, for the one piece put up for sale

init_wallets

say "funding the issuer and the buyer from the treasury"
fund "$ISSUER" 8 480000
fund "$BUYER"  3 80000000

# One pin for the whole collection, computed before any piece is named.
say "the shared document — one uri for every member"
URI=$(pin "$DOC")
echo "   uri $URI"
uri_budget "$DOC"

LAST=$(( FROM + COUNT - 1 ))
FOR_SALE=""      # asset_id of the first piece this run mints and does NOT pay away
FOR_SALE_INDEX=""

say "minting pieces $FROM..$LAST of $COLLECTION"
for i in $(seq "$FROM" "$LAST"); do
  # --max-supply 1 and --decimals 0 are what make the piece unique and indivisible, and both are
  # hashed into asset_id through `terms`. --index is the loop variable and never a constant,
  # because item.image is an {index} template and digests[i] pins the image for index i: two
  # pieces sharing an index would be two distinct assets resolving to one picture, both claiming
  # to be pinned. The CLI refuses a unique item in a --collection with no --index for that reason.
  read -r AID MID <<<"$(issue_public "PON#$i" 1 1 "$COLLECTION" "$i")"
  mine 2
  name_asset "$AID" "$MID" "Phases of One Night #$i" "PON" 0 "$URI"
  if [ "$i" -lt $(( FROM + PAY_FIRST )) ]; then
    bury
    pay_asset "$AID" 1 "$RECIPIENT"
    mine 2
    echo "   #$i $AID -> recipient"
  else
    mine 2
    echo "   #$i $AID (held by the issuer)"
    if [ -z "$FOR_SALE" ]; then FOR_SALE="$AID"; FOR_SALE_INDEX="$i"; fi
  fi
done
bury

if [ "$NO_SALE" = 0 ] && [ -n "$FOR_SALE" ]; then
  sell_and_buy "$FOR_SALE" 1 "$PRICE" "Phases of One Night #$FOR_SALE_INDEX"
elif [ "$NO_SALE" = 0 ]; then
  echo "   (no unsold piece left to put up for sale: --count is not greater than $PAY_FIRST)"
fi

say "done — what the channel says about the collection now"
echo "   $NJ_BIN assets --uivk <channel uivk> --keys $KEYS"
echo "   curl -s http://127.0.0.1:8787/api/assets | jq '.items[] | select(.symbol==\"PON\")'"
echo
echo '   The member count is a property of the channel, not of pon/c.json. "size" there is the'
echo '   number the publisher intends; a wallet MUST NOT treat a member whose index is >= size'
echo '   as invalid (spec/asset-collection-v0.md section 3.1). Ask the channel.'

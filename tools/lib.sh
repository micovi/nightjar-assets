#!/usr/bin/env bash
# Shared plumbing for nc/mint.sh and pon/mint.sh.
#
# The split is deliberate. **This file is housekeeping for whatever network you run it against**:
# funding a wallet, mining enough blocks, parsing ids out of CLI output. **The mint scripts are
# the lesson**: which messages are sent, in which order, and why. If you are reading to learn
# what deploying an asset costs in messages, read nc/mint.sh and pon/mint.sh; this file is what
# makes them run.
#
# Nothing here is Nightjar-specific except the CLI invocations, and every one of those is taken
# from a script in the Nightjar repository that runs against a live node:
# `scripts/devnet-testassets.sh` (issue, name, pay) and `scripts/devnet-programs.sh`
# (sell, publish, orders, buy).

# ---------------------------------------------------------------------------------------------
# Options every mint script takes. Parsed here so the two scripts cannot drift.
# shellcheck disable=SC2034  # COUNT, FROM and NO_SALE are read by the sourcing script

BASE_URL=""
RECIPIENT=""
DRY_RUN=0
ALLOW_UNPINNED=0
DEVNET="${NIGHTJAR_DEVNET_DIR:-.devnet}"
NJ_BIN="${NIGHTJAR_BIN:-./target/release/nightjar}"
COUNT=10
FROM=0
NO_SALE=0

# `spec/transition-v0.md` section 7: the wallet builds only against blocks a reorganization can
# no longer move, so a note is invisible to the next command until it is FINALITY_DEPTH deep.
# Mint and immediately pay and you get a confusing `insufficient funds: 0 ... available` several
# steps from its cause. The node reports its own depth — `curl -s $INDEXER/api/status | jq
# .info.finality_depth` — and this default is deliberately two more than the 10 it reports.
FINALITY="${FINALITY:-12}"

usage_common() {
  cat <<'USAGE'
  --base-url URL     where the documents of THIS repository are published, ending in `/`
                     e.g. https://raw.githubusercontent.com/you/nightjar-assets/main/
                     The document must already be reachable there: the pin is computed from
                     the published bytes, before the naming message is signed.
  --recipient NJ     an `nj…` address to pay to. Default: the buyer wallet this script creates.
  --dry-run          print the exact sequence of messages and send nothing. Needs no node.
  --allow-unpinned   name the asset with a bare `uri`, with no `#b2=`. PERMANENT: see README.md.
  --devnet DIR       where the wallets and proving keys live.
                     default .devnet (or $NIGHTJAR_DEVNET_DIR)
  --bin PATH         default ./target/release/nightjar (or $NIGHTJAR_BIN)
  --no-sale          stop after issue/name/pay; do not post a sale or buy it
USAGE
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --base-url)       BASE_URL="$2"; shift 2 ;;
      --recipient)      RECIPIENT="$2"; shift 2 ;;
      --dry-run)        DRY_RUN=1; shift ;;
      --allow-unpinned) ALLOW_UNPINNED=1; shift ;;
      --devnet)         DEVNET="$2"; shift 2 ;;
      --bin)            NJ_BIN="$2"; shift 2 ;;
      --count)          COUNT="$2"; shift 2 ;;
      --from)           FROM="$2"; shift 2 ;;
      --no-sale)        NO_SALE=1; shift ;;
      -h|--help)        usage; exit 0 ;;
      *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
  done
  case "$BASE_URL" in
    "")  fail "--base-url is required. There is no default: a worked example that only works for one GitHub account is a worked example for nobody." ;;
    */)  ;;
    *)   BASE_URL="$BASE_URL/" ;;
  esac
}

fail() { echo "FAIL: $*" >&2; exit 1; }
say()  { printf '\n== %s\n' "$*"; }

# Shell-quote an argument only when it needs it, so the echoed command can be pasted.
q() {
  local a out=""
  for a in "$@"; do
    case "$a" in
      *[![:alnum:]_./:@=+-]*) out="$out '$a'" ;;
      *) out="$out $a" ;;
    esac
  done
  printf '%s' "${out# }"
}

# Echo a command, then run it — or, under --dry-run, echo it and stop. The command blocks in the
# READMEs of this repository are `--dry-run` output, which is why they are not guesses.
# Stdout is dropped: these commands report at length and the sequence is the thing being shown.
run() {
  printf '   $ %s\n' "$(q "$@")"
  [ "$DRY_RUN" = 1 ] && return 0
  "$@" >/dev/null
}

# ---------------------------------------------------------------------------------------------
# The two limits that decide what a document's path may be.

# `spec/transition-v0.md` section 9: `uri_len` is at most 255 bytes of US-ASCII.
# It is the ceiling of the wire format rather than a chosen number: `uri_len` is a `u8`, so
# nothing larger can be expressed on the wire at all.
URI_MAX=255
# `spec/asset-metadata-v0.md` section 2.1: `#b2=` plus 43 base64url characters of digest.
PIN_COST=47

# **Pin the document in the uri the `ASSET` message signs**, and compute the pin from the
# *published* bytes rather than from the working copy.
#
# `spec/asset-metadata-v0.md` section 2.1: the signature on an `ASSET` message covers the
# pointer, not the bytes at the end of it. A `#b2=` fragment carrying the BLAKE2b-256 of the
# document makes the signed `uri` fix the bytes. Without it the document is revocable by whoever
# controls the host — a different party from whoever signed the asset — and a wallet has no way
# to notice the swap.
#
# It is not optional in practice, because `ASSET` is **first-valid-wins**
# (`spec/transition-v0.md` section 9 step 4): a verifier that already holds `asset_id` in
# `Assets` ignores every later message naming it, and that map is never pruned. An asset named
# with a bare uri can never be re-named with a pinned one. The first naming is the only one.
# That is why this function fails rather than warning, and why `--allow-unpinned` exists as an
# explicit, loud opt-out rather than a silent fallback.
# What a pinned document costs of the uri a signature will cover. The arithmetic is the reason
# the two example directories are called `nc` and `pon` and not something readable; README.md
# does it in full.
uri_budget() {  # url
  printf '   %s bytes of path + %s for the pin = %s of the %s an ASSET message allows\n' \
    "${#1}" "$PIN_COST" "$(( ${#1} + PIN_COST ))" "$URI_MAX"
}

# The budget half of `pin`, on its own, because one uri in these documents is pinned without
# being signed. `digest_table.uri` (`spec/asset-collection-v0.md` section 3.3.1) is named by the
# collection document and hashed by the `b2` beside it, and no `ASSET` message ever carries it —
# so nothing in the wire format forces it under 255 bytes. It is held to the same ceiling anyway.
# The table and the document are published side by side and a publisher who discovers the limit
# on one of them wants to discover it on both at the same moment, which is while the layout can
# still be moved (README.md: "why you only get to choose it once").
uri_fits() {  # url
  local budget=$(( ${#1} + PIN_COST ))
  [ "$budget" -le "$URI_MAX" ] || fail "the pinned uri is $budget bytes, over the $URI_MAX-byte limit of an ASSET message ($1). Shorten the path or the base URL; see README.md."
}

# BLAKE2b-256 of a **local** file, base64url unpadded. Every digest these documents carry is in
# this encoding — `logo.b2`, `digests[i]` and `digest_table.b2` — and tools/doc.py computes them
# the same way, which is why a digest printed by one can be compared against a digest printed by
# the other without anybody converting anything.
b2_file() {  # path -> digest
  python3 -c 'import sys,hashlib,base64; d=hashlib.blake2b(open(sys.argv[1],"rb").read(),digest_size=32).digest(); print(base64.urlsafe_b64encode(d).decode().rstrip("="))' "$1"
}

pin() {   # url -> url#b2=<digest>
  local url="$1" b2
  uri_fits "$url"

  if [ "$ALLOW_UNPINNED" = 1 ]; then
    echo "WARNING: naming with a bare uri. ASSET is first-valid-wins, so this asset can NEVER be re-named with a pinned one." >&2
    printf '%s' "$url"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s#b2=<blake2b-256 of the document published at that url>' "$url"
    return 0
  fi

  b2=$(curl -sfL --max-time 20 "$url" \
    | python3 -c "import sys,hashlib,base64; d=hashlib.blake2b(sys.stdin.buffer.read(),digest_size=32).digest(); print(base64.urlsafe_b64encode(d).decode().rstrip('='))" 2>/dev/null) || b2=""
  [ -n "$b2" ] || fail "could not fetch $url to pin it. Publish the documents first: the pin is computed from the published bytes, and the naming message that signs it can only be sent once. Or pass --allow-unpinned and read what that costs."
  printf '%s#b2=%s' "$url" "$b2"
}

# **The digest table is published before the document, and this is what checks that it was.**
#
# `spec/asset-collection-v0.md` section 3.3.1: a collection too large to carry `digests` inline —
# 51 bytes a member reaches the 16 KiB document limit at 302 — may instead carry
#
#     "digest_table": { "uri": "…/pon/d.bin", "b2": "…", "count": 10000 }
#
# a file of exactly `32 × count` raw bytes, with no header and no magic number, whose digest
# lives **inside the document**. That last clause fixes the order of everything a mint script
# does, because a document that contains the table's digest cannot be written until the table is
# final:
#
#   1. publish the table           its bytes are what the document will commit to
#   2. b2 into the document        `tools/doc.py digests --table` writes it
#   3. publish the document        now, and not before, the document is final
#   4. pin the document            the `#b2=` covers the b2 from step 2
#   5. name every member with it   one signed uri for the whole collection, section 2
#
# **Do 3 before 1 and nothing anywhere reports it.** The pin is honest — it matches the document
# a wallet fetches — the naming message is accepted, and `ASSET` is first-valid-wins
# (`spec/transition-v0.md` section 9 step 4), so there is no second one. The wallet then fetches
# the table, finds a `b2` that does not match, and does what section 3.3.1 requires: treats **the
# whole collection** as unpinned rather than some members. Every image, silently, for good. A
# `digests` array cannot fail this way because it has no second file to be out of step with, so
# this is a failure mode the table introduces and the only thing that costs.
#
# Which is why this fails rather than warning, exactly as `pin` does and for the same reason:
# the last moment the mistake is cheap is before the first naming message is signed.
check_published_table() {  # uri b2 count
  local uri="$1" want="$2" count="$3" tmp n got
  uri_fits "$uri"
  uri_budget "$uri"

  if [ "$DRY_RUN" = 1 ]; then
    printf '   (fetch %s and check it is exactly 32 x %s = %s bytes hashing to %s)\n' \
      "$uri" "$count" "$(( count * 32 ))" "$want"
    return 0
  fi

  tmp=$(mktemp "${TMPDIR:-/tmp}/nj-digest-table.XXXXXX")
  curl -sfL --max-time 20 -o "$tmp" "$uri" || { rm -f "$tmp"; fail "could not fetch the digest table at $uri. Publish the TABLE first and the document second: the document carries the table's b2, so a document published against a table that is not up yet pins bytes nobody will be served."; }
  n=$(wc -c <"$tmp" | tr -d '[:space:]')
  got=$(b2_file "$tmp")
  rm -f "$tmp"

  # Length before digest, in that order, because they fail differently. A table of the wrong
  # length has no well-defined entry at offset 32i at all, so it is not a table with some bad
  # digests; it is a file. Section 3.3.1 has a wallet reject it on the length alone.
  [ "$n" = "$(( count * 32 ))" ] || fail "the table published at $uri is $n bytes, and section 3.3.1 requires exactly 32 x count = 32 x $count = $(( count * 32 )). A wallet rejects it on the length before it reads a digest out of it, and treats every member as unpinned."
  [ "$got" = "$want" ] || fail "the table published at $uri hashes to $got and the document pins $want. The table that is up is not the one the document was written against — publish the table, then re-run tools/doc.py digests --table, then publish the document, then run this."

  printf '   table %s bytes = 32 x %s, b2 %s — the bytes the document pins\n' "$n" "$count" "$got"
}

# **The items document is published before the document too, and for the same reason.**
#
# `spec/asset-collection-v0.md` section 3.4.1: a collection whose per-piece detail will not fit
# inline — `items` costs about 190 bytes a member, so one attribute each runs a collection out of
# the 16 KiB document at 81 — may instead carry
#
#     "items_document": { "uri": "…/pon/i.json", "b2": "…", "bytes": 1900000 }
#
# a file whose top level is the `items` array itself, with nothing wrapping it. Its digest lives
# **inside the document**, exactly as the table's does, so the order is the order above with one
# more file in it: publish the items document, write its b2 and its length into the document,
# publish that, pin it, then name every member with the pinned uri.
#
# **`bytes` is the one thing this shape has that the table does not, and it is not a convenience.**
# The table's length is `32 × count` and derivable; an items document's is not, so it is declared —
# and declared so that a wallet knows what the fetch costs *before* it makes it and can refuse
# rather than find out by having downloaded it. What makes the declaration worth anything is that
# section 3.4.1 has a wallet refuse a body of any other length. So a `bytes` that is merely stale —
# the right file, one character longer — is refused exactly as firmly as a swapped one, and it is
# the easiest of these mistakes to make: edit one trait, republish the file, forget the document.
#
# Which is why this fails rather than warning, as `pin` and `check_published_table` do and for the
# reason they do: `ASSET` is first-valid-wins (`spec/transition-v0.md` section 9 step 4), so the
# last moment any of it is cheap is before the first naming message is signed.
check_published_items() {  # uri b2 bytes
  local uri="$1" want="$2" declared="$3" tmp n got
  uri_fits "$uri"
  uri_budget "$uri"

  if [ "$DRY_RUN" = 1 ]; then
    printf '   (fetch %s and check it is exactly %s bytes hashing to %s)\n' "$uri" "$declared" "$want"
    return 0
  fi

  tmp=$(mktemp "${TMPDIR:-/tmp}/nj-items-document.XXXXXX")
  curl -sfL --max-time 20 -o "$tmp" "$uri" || { rm -f "$tmp"; fail "could not fetch the items document at $uri. Publish the ITEMS DOCUMENT first and the collection document second: the collection document carries its b2 and its exact length, so a document published against an items document that is not up yet declares bytes nobody will be served."; }
  n=$(wc -c <"$tmp" | tr -d '[:space:]')
  got=$(b2_file "$tmp")
  rm -f "$tmp"

  # Length before digest, in that order and for a sharper reason than the table's: a wallet does
  # not hash a body it has refused. Section 3.4.1 has it reject on the length alone, so a length
  # reported as a digest failure would send a publisher looking for the wrong mistake.
  [ "$n" = "$declared" ] || fail "the items document published at $uri is $n bytes and the collection document declares $declared. Section 3.4.1 has a wallet refuse a body whose length is not exactly items_document.bytes — re-run tools/doc.py items --external, publish the file, then publish the document, then run this."
  [ "$got" = "$want" ] || fail "the items document published at $uri hashes to $got and the collection document pins $want. The file that is up is not the one the document was written against — publish the file, then re-run tools/doc.py items --external, then publish the document, then run this."

  printf '   items document %s bytes, b2 %s — the bytes the document pins\n' "$n" "$got"
}

# ---------------------------------------------------------------------------------------------
# Node plumbing.

preflight() {
  [ "$DRY_RUN" = 1 ] && return 0
  [ -x "$NJ_BIN" ]        || fail "no $NJ_BIN — in the Nightjar repository: cargo build --release -p nightjar-cli"
  [ -d "$KEYS" ]          || fail "no $KEYS — run: $NJ_BIN zk-setup --keys $KEYS"
  [ -d "$DEVNET/channel" ]|| fail "no $DEVNET/channel — start the local network from infra/README.md in the Nightjar repository"
}

nj()   { "$NJ_BIN" "$@"; }
mine() { [ "$DRY_RUN" = 1 ] && { printf '   (mine %s blocks)\n' "$1"; return 0; }; nj mine "$1" >/dev/null || fail "mine $1"; sleep 2; }
bury() { mine "$FINALITY"; }

# One note per message the run will send. A spend's change is unconfirmed until the next block
# and these messages go out back to back, so a wallet holding one big note stops after the first
# send with `InsufficientFunds { available: 0 }`. `send` pays `--value` to `--to` once per
# fragment, so a body long enough to need N fragments buys N separate notes.
fund() {  # dir notes zatoshi-per-note
  local dir="$1" n="$2" value="$3" ua body
  [ "$DRY_RUN" = 1 ] && { printf '   (fund %s with %s notes of %s zatoshi from the treasury)\n' "$dir" "$n" "$value"; return 0; }
  [ "$n" -ge 2 ] || n=2; [ "$n" -le 8 ] || n=8
  ua=$(nj wallet --dir "$dir" address | tail -1)
  body=$(printf "%0*d" $(( ((n - 1) * 466 + 1) * 2 )) 0)
  nj wallet --dir "$TREASURY" send --to "$ua" --uivk "$TREASURY_UIVK" --body-hex "$body" --value "$value" >/dev/null \
    || fail "the treasury $TREASURY could not fund $dir — point NIGHTJAR_DEVNET_TREASURY at a funded wallet"
  mine 2
}

# `issue` prints `... msg_id <64 hex> ...` on its first line and `asset_id <64 hex>` on a later
# one. Parsing both rather than re-deriving them keeps this script honest about what actually
# landed instead of computing what should have.
#
# `--index` is always passed, and in pon/mint.sh it is the piece number and never a constant. A
# collection document's `item.image` is an `{index}` template (`spec/asset-collection-v0.md`
# section 3.2) and `digests[i]` pins the image for index `i`, so two pieces sharing an index are
# two distinct assets that resolve to one picture and both claim to be pinned. The CLI refuses a
# unique item in a `--collection` with no `--index` for that reason.
# The command echo goes to **stderr** here and nowhere else in this file, because this function's
# stdout is captured by its caller: an echo on stdout would be read back as an asset id.
issue_public() {  # label amount max_supply collection index -> "asset_id msg_id"
  local out mid aid
  printf '   $ %s\n' "$(q "$NJ_BIN" wallet --dir "$ISSUER" issue --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" \
    --keys "$KEYS" --label "$1" --amount "$2" --public --max-supply "$3" \
    ${4:+--collection "$4"} --index "${5:-0}")" >&2
  if [ "$DRY_RUN" = 1 ]; then printf '<asset_id> <issuance_msg_id>'; return 0; fi
  out=$(nj wallet --dir "$ISSUER" issue --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" --keys "$KEYS" \
    --label "$1" --amount "$2" --public --max-supply "$3" \
    ${4:+--collection "$4"} --index "${5:-0}" 2>&1) || { echo "$out" >&2; fail "issue $1"; }
  mid=$(printf '%s' "$out" | sed -n 's/.*msg_id \([0-9a-f]\{64\}\).*/\1/p' | head -1)
  aid=$(printf '%s' "$out" | sed -n 's/^ *asset_id *\([0-9a-f]\{64\}\).*/\1/p' | head -1)
  [ -n "$mid" ] && [ -n "$aid" ] || { echo "$out" >&2; fail "could not read ids from issue $1"; }
  printf '%s %s' "$aid" "$mid"
}

name_asset() {    # asset_id issuance_msg_id name symbol decimals uri
  run "$NJ_BIN" wallet --dir "$ISSUER" name-asset --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" \
    --asset "$1" --issuance "$2" --name "$3" --symbol "$4" --decimals "$5" --uri "$6" \
    || fail "name-asset $3 — if it reports the asset is already named, ASSET is first-valid-wins and there is no second chance (spec/transition-v0.md section 9 step 4)"
}

pay_asset() {     # asset_id amount recipient
  run "$NJ_BIN" wallet --dir "$ISSUER" pay --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" --keys "$KEYS" \
    --asset "$1" --amount "$2" --recipient "$3" || fail "pay $1"
}

# The newest open order for an asset. `tail -1` over the whole list is not that: the list is in
# position order but carries every kind of published note, so its last line is simply the last
# thing anyone published to the channel.
newest_open() {   # asset_id -> position
  [ "$DRY_RUN" = 1 ] && { printf '<position of the published order>'; return 0; }
  nj wallet --dir "$ISSUER" orders --uivk "$CHANNEL_UIVK" --keys "$KEYS" \
    | awk -v a="$1" '$3=="open" && $5==a {print $2}' | sort -n | tail -1
}

# ---------------------------------------------------------------------------------------------
# Wallets. Set up after parse_args, because they depend on --devnet.

init_wallets() {
  KEYS="$DEVNET/keys"
  ISSUER="${NIGHTJAR_EXAMPLE_ISSUER:-$DEVNET/examples-issuer}"
  BUYER="${NIGHTJAR_EXAMPLE_BUYER:-$DEVNET/examples-buyer}"
  TREASURY="${NIGHTJAR_DEVNET_TREASURY:-$DEVNET/treasury}"
  preflight
  if [ "$DRY_RUN" = 1 ]; then
    CHANNEL_UA="<channel UA>"; CHANNEL_UIVK="<channel uivk>"
    BUYER_NJ="<buyer nj-address>"; TREASURY_UIVK="<treasury uivk>"
    [ -n "$RECIPIENT" ] || RECIPIENT="$BUYER_NJ"
    return 0
  fi
  mkdir -p "$ISSUER" "$BUYER"
  CHANNEL_UA=$(nj wallet --dir "$DEVNET/channel" address | tail -1)
  CHANNEL_UIVK=$(nj wallet --dir "$DEVNET/channel" uivk | tail -1)
  TREASURY_UIVK=$(nj wallet --dir "$TREASURY" uivk | tail -1)
  nj wallet --dir "$ISSUER" nj-address >/dev/null
  BUYER_NJ=$(nj wallet --dir "$BUYER" nj-address | sed -n 's/^address //p')
  [ -n "$BUYER_NJ" ] || fail "could not read the buyer's nj-address"
  [ -n "$RECIPIENT" ] || RECIPIENT="$BUYER_NJ"
}

# ---------------------------------------------------------------------------------------------
# The sale. Identical for both examples, which is the point: `programs::reservation_policy`
# sells a lot of a fungible asset and a unique piece of art with the same four lines.
#
#     pk(S) && after(h_exp) || (pk(B) && zec(H_order, price))
#
# See nc/sale.roost for what each half is load-bearing for, and `roost check` it.
sell_and_buy() {  # asset_id amount price_zatoshi what
  local asset="$1" amount="$2" price="$3" what="$4" sell order_uivk position

  say "$what: the issuer reserves it for the buyer at $price zatoshi"
  # `--buyer` is not optional and `wallet sell` has no openly fillable form (F5): ZEC never
  # enters Nightjar, so on an open order two buyers filling in one block both pay and only the
  # lower (height, tx_index) is applied. `--expires-in` time-locks the seller's cancel, without
  # which the seller reads the payment in the mempool and self-spends at a higher fee.
  printf '   $ %s\n' "$(q "$NJ_BIN" wallet --dir "$ISSUER" sell --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" \
    --keys "$KEYS" --asset "$asset" --amount "$amount" --price "$price" --buyer "$BUYER_NJ" --expires-in 500)"
  if [ "$DRY_RUN" = 1 ]; then
    order_uivk="<the PER-ORDER uivk sell prints>"
  else
    sell=$(nj wallet --dir "$ISSUER" sell --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" --keys "$KEYS" \
      --asset "$asset" --amount "$amount" --price "$price" --buyer "$BUYER_NJ" --expires-in 500) \
      || fail "sell $asset"
    order_uivk=$(printf '%s' "$sell" | sed -n 's/^ *seller-uivk *//p')
    [ -n "$order_uivk" ] || fail "sell printed no per-order uivk"
    # F16: the order's viewing key must not be the issuer's account-level one. A published NOTE
    # carries the policy verbatim, and a `zec()` leaf naming the account key would hand every
    # reader of the channel the incoming viewing key to that wallet's whole Zcash history.
    [ "$order_uivk" != "$(nj wallet --dir "$ISSUER" uivk | tail -1)" ] \
      || fail "sell published the account-level uivk (F16)"
  fi
  bury

  # Until it is published the reservation is a note only the issuer can see. A `NOTE` message
  # (kind 0x03) is what puts it in the order book — it changes no state a verifier consults and
  # is purely how a maker tells a buyer where to look (`spec/transition-v0.md` section 7).
  say "$what: publishing the reservation so the buyer can find it"
  run "$NJ_BIN" wallet --dir "$ISSUER" publish --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" --keys "$KEYS" \
    || fail "publish"
  bury

  position=$(newest_open "$asset")
  [ -n "$position" ] || fail "the reservation did not appear as an open order"

  say "$what: the BUYER pays and takes it"
  # The ZEC is paid in the very Zcash transaction that carries the transition, and the verifier
  # checks the claim against that transaction. `--seller-uivk` is the per-order key the seller
  # printed; the buyer needs it to construct a payment the claim will recognise.
  run "$NJ_BIN" wallet --dir "$BUYER" buy --to "$CHANNEL_UA" --uivk "$CHANNEL_UIVK" --keys "$KEYS" \
    --position "$position" --seller-uivk "$order_uivk" || fail "buy $asset"
  bury
}

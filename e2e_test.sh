#!/bin/bash
set -e

BASE="https://dev.prime-pay.org"
CT="Content-Type: application/json"

echo "============================================"
echo "  E2E Test: Full Payin Order Flow"
echo "============================================"

# ── Step 0: Login as admin ──────────────────────────
echo -e "\n>>> Step 0: Login as admin"
LOGIN=$(curl -sf -X POST "$BASE/api/v1/auth/login" \
  -H "$CT" -d '{"username":"admin","password":"admin123"}')
TOKEN=$(echo "$LOGIN" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"
echo "OK — token acquired"

# ── Step 1: Create trader user ──────────────────────
echo -e "\n>>> Step 1: Create trader"
TRADER=$(curl -sf -X POST "$BASE/api/v1/auth/register" \
  -H "$AUTH" -H "$CT" \
  -d '{"username":"e2e_trd_'$RANDOM'","password":"pass123","role":"trader"}')
TRADER_ID=$(echo "$TRADER" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "OK — trader user_id=$TRADER_ID"

# ── Step 2: Create merchant user ────────────────────
echo -e "\n>>> Step 2: Create merchant"
MERCH=$(curl -sf -X POST "$BASE/api/v1/auth/register" \
  -H "$AUTH" -H "$CT" \
  -d '{"username":"e2e_mrc_'$RANDOM'","password":"pass123","role":"merchant"}')
MERCH_USER_ID=$(echo "$MERCH" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "OK — merchant user_id=$MERCH_USER_ID"

# ── Step 3: Get merchant profile ────────────────────
echo -e "\n>>> Step 3: Get merchant details"
MERCHANTS=$(curl -sf "$BASE/api/v1/merchants/?search=e2e_mrc" -H "$AUTH")
MERCHANT_ID=$(echo "$MERCHANTS" | python3 -c "
import sys,json
ms = json.load(sys.stdin)
m = next((x for x in ms if x['user_id'] == $MERCH_USER_ID), None)
print(m['id'] if m else 'NOT_FOUND')
")
echo "OK — merchant_id=$MERCHANT_ID"

# ── Step 4: Configure merchant ──────────────────────
echo -e "\n>>> Step 4: Configure merchant (fees, rate)"
curl -sf -X PATCH "$BASE/api/v1/merchants/$MERCHANT_ID" \
  -H "$AUTH" -H "$CT" \
  -d '{
    "status": "enabled",
    "fees": {"sbp": 5},
    "order_ttl_seconds": 900
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — status={d[\"status\"]}, fees={d[\"fees\"]}')"

# ── Step 5: Get merchant API key ────────────────────
echo -e "\n>>> Step 5: Reset merchant API key"
KEY_RESP=$(curl -sf -X POST "$BASE/api/v1/merchants/$MERCHANT_ID/api-key/reset" -H "$AUTH")
API_KEY=$(echo "$KEY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")
echo "OK — api_key=$API_KEY"

# ── Step 6: Enable trader and configure methods ─────
echo -e "\n>>> Step 6: Configure trader"
TRADERS=$(curl -sf "$BASE/api/v1/traders/" -H "$AUTH")
TRADER_PROFILE_ID=$(echo "$TRADERS" | python3 -c "
import sys,json
ts = json.load(sys.stdin)
t = next((x for x in ts if x['user_id'] == $TRADER_ID), None)
print(t['id'] if t else 'NOT_FOUND')
")
echo "trader profile id=$TRADER_PROFILE_ID"

curl -sf -X PATCH "$BASE/api/v1/traders/$TRADER_PROFILE_ID" \
  -H "$AUTH" -H "$CT" \
  -d '{
    "status": "enabled",
    "is_payin_active": true,
    "method_configs": [
      {"payment_method": "sbp", "fee": 1.0, "min_amount": 0, "max_amount": 100000, "is_active": true}
    ]
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — status={d[\"status\"]}, is_payin_active={d[\"is_payin_active\"]}')"

# ── Step 7: Create requisite for trader ─────────────
echo -e "\n>>> Step 7: Create requisite"
# Login as trader to create requisite
TLOGIN=$(curl -sf -X POST "$BASE/api/v1/auth/login" \
  -H "$CT" -d "{\"username\":\"$(echo $TRADER | python3 -c "import sys,json; print(json.load(sys.stdin)['username'])")\",\"password\":\"pass123\"}")
TTOKEN=$(echo "$TLOGIN" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
TAUTH="Authorization: Bearer $TTOKEN"

REQ=$(curl -sf -X POST "$BASE/api/v1/requisites/my" \
  -H "$TAUTH" -H "$CT" \
  -d '{
    "bank_name": "TestBank",
    "account_number": "40817810099910004312",
    "account_holder": "Test Account",
    "payment_method": "sbp",
    "currency": "RUB"
  }')
REQ_ID=$(echo "$REQ" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "OK — requisite_id=$REQ_ID"

# Enable the requisite (default status is DISABLED)
echo -e "\n>>> Step 7b: Enable requisite"
curl -sf -X PATCH "$BASE/api/v1/requisites/$REQ_ID" \
  -H "$AUTH" -H "$CT" \
  -d '{"status": "enabled"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — status={d[\"status\"]}')"

# ── Step 8: Deposit USDT to trader balance ──────────
echo -e "\n>>> Step 8: Deposit to trader WORK balance"
DEP=$(curl -sf -X POST "$BASE/api/v1/finances/admin/deposit?user_id=$TRADER_ID&amount=1000" \
  -H "$AUTH" -H "$CT")
echo "$DEP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — deposited {d[\"amount\"]} USDT')"

# Verify balance
echo -e "\n>>> Step 8b: Verify trader balance"
curl -sf "$BASE/api/v1/finances/my-balances" -H "$TAUTH" | python3 -c "
import sys,json
bs = json.load(sys.stdin)
for b in bs:
    print(f'  {b[\"type\"]} {b[\"currency\"]}: {b[\"amount\"]}')
"

# ── Step 9: Check rate config exists ────────────────
echo -e "\n>>> Step 9: Check rates"
curl -sf "$BASE/api/v1/rates/" -H "$AUTH" | python3 -c "
import sys,json
rates = json.load(sys.stdin)
for r in rates:
    print(f'  #{r[\"id\"]} {r[\"name\"]}: {r[\"fiat_currency\"]} rate={r[\"current_rate\"]} active={r[\"is_active\"]}')
if not rates:
    print('  NO RATES CONFIGURED!')
"

# ── Step 10: Create payin order ─────────────────────
echo -e "\n>>> Step 10: Create PAYIN order (1000 RUB via SBP)"
ORDER=$(curl -s -w "\n%{http_code}" -X POST "$BASE/api/merchant/v1/orders/payin" \
  -H "X-Api-Key: $API_KEY" -H "$CT" \
  -d '{
    "amount": 1000,
    "currency": "RUB",
    "payment_method": "sbp",
    "internalId": "e2e_'$RANDOM'"
  }')
HTTP_CODE=$(echo "$ORDER" | tail -1)
BODY=$(echo "$ORDER" | sed '$d')
echo "HTTP $HTTP_CODE"
echo "$BODY" | python3 -m json.tool
ORDER_UUID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id','FAILED'))" 2>/dev/null || echo "FAILED")
echo "order uuid=$ORDER_UUID"

if [ "$ORDER_UUID" = "FAILED" ]; then
  echo "!!! ORDER CREATION FAILED — aborting"
  exit 1
fi

# ── Step 11: Check order as trader ──────────────────
echo -e "\n>>> Step 11: Check order from trader side"
curl -sf "$BASE/api/v1/orders/my-active" -H "$TAUTH" | python3 -c "
import sys,json
orders = json.load(sys.stdin)
for o in orders:
    print(f'  Order #{o[\"id\"]} uuid={o[\"uuid\"]} status={o[\"status\"]} amount={o[\"amount\"]} {o[\"currency\"]}')
"

# ── Step 12: Complete order (trader confirms) ───────
echo -e "\n>>> Step 12: Trader confirms payment received"
# Get order id (internal) from trader list
ORDER_INTERNAL_ID=$(curl -sf "$BASE/api/v1/orders/my-active" -H "$TAUTH" | python3 -c "
import sys,json
orders = json.load(sys.stdin)
if orders:
    print(orders[0]['id'])
else:
    print('NONE')
")
echo "internal order_id=$ORDER_INTERNAL_ID"

if [ "$ORDER_INTERNAL_ID" != "NONE" ]; then
  curl -sf -X POST "$BASE/api/v1/orders/$ORDER_INTERNAL_ID/success" \
    -H "$TAUTH" -H "$CT" | python3 -c "
import sys,json
o = json.load(sys.stdin)
print(f'OK — status={o[\"status\"]}')
"
fi

# ── Step 13: Verify final balances ──────────────────
echo -e "\n>>> Step 13: Final balances"
echo "Trader:"
curl -sf "$BASE/api/v1/finances/my-balances" -H "$TAUTH" | python3 -c "
import sys,json
for b in json.load(sys.stdin):
    print(f'  {b[\"type\"]} {b[\"currency\"]}: {b[\"amount\"]}')
"

echo "All balances:"
curl -sf "$BASE/api/v1/finances/balances" -H "$AUTH" | python3 -c "
import sys,json
for b in json.load(sys.stdin):
    if b['amount'] != 0:
        kind = 'system' if b['is_system'] else f'user={b[\"user_id\"]}' if b['user_id'] else f'merchant={b[\"merchant_id\"]}'
        print(f'  [{kind}] {b[\"type\"]} {b[\"currency\"]}: {b[\"amount\"]}')
"

echo -e "\n>>> Step 14: Check ledger entries"
curl -sf "$BASE/api/v1/finances/ledger?limit=10" -H "$AUTH" | python3 -c "
import sys,json
for e in json.load(sys.stdin):
    print(f'  #{e[\"id\"]} {e[\"reference_type\"]} {e[\"amount\"]} {e[\"currency\"]} | {e[\"description\"]}')
"

echo -e "\n============================================"
echo "  E2E Test Complete!"
echo "============================================"

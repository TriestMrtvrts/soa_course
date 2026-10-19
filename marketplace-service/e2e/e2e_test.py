"""End-to-end check of the running stack (docker compose up). Stdlib only.

Usage: python3 marketplace-service/e2e/e2e_test.py [--wait] [base_url]
Requires ORDER_RATE_LIMIT_MINUTES = 1 (default). --wait additionally sleeps past the rate-limit
window to check ORDER_HAS_ACTIVE and the second UPDATE_ORDER.
"""
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import time

WAIT = "--wait" in sys.argv
ARGS = [a for a in sys.argv[1:] if a != "--wait"]
BASE = ARGS[0] if ARGS else "http://localhost:8080"
RUN = uuid.uuid4().hex[:6].upper()
passed = 0


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or "null"), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or "null"), err.headers


def check(name, method, path, body=None, token=None, status=200, code=None):
    global passed
    got_status, payload, headers = call(method, path, body, token)
    ok = got_status in (status if isinstance(status, tuple) else (status,)) and (code is None or (payload or {}).get("error_code") == code)
    assert headers.get("X-Request-Id"), f"{name}: no X-Request-Id"
    mark = "OK " if ok else "FAIL"
    print(f"[{mark}] {name}: {got_status} {payload.get('error_code', '') if isinstance(payload, dict) else ''}")
    if not ok:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(1)
    passed += 1
    return payload


def user(role="USER"):
    email = f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    check(f"register {role}", "POST", "/auth/register", {"email": email, "password": "Passw0rd!", "role": role}, status=201)
    tokens = check(f"login {role}", "POST", "/auth/login", {"email": email, "password": "Passw0rd!"})
    return tokens


admin = check("login ADMIN", "POST", "/auth/login", {"email": "admin@marketplace.dev", "password": "Admin12345"})["access_token"]
seller_tokens = user("SELLER")
seller = seller_tokens["access_token"]
seller2 = user("SELLER")["access_token"]

# ---------------------------------------------------------------- auth
check("no token", "GET", "/products", status=401, code="TOKEN_INVALID")
check("garbage token", "GET", "/products", token="abc.def.ghi", status=401, code="TOKEN_INVALID")
check("refresh token used as access", "GET", "/products", token=seller_tokens["refresh_token"], status=401, code="TOKEN_INVALID")
check("refresh ok", "POST", "/auth/refresh", {"refresh_token": seller_tokens["refresh_token"]})
check("refresh invalid", "POST", "/auth/refresh", {"refresh_token": "nope"}, status=401, code="REFRESH_TOKEN_INVALID")
check("wrong password", "POST", "/auth/login", {"email": "admin@marketplace.dev", "password": "x"}, status=401, code="INVALID_CREDENTIALS")
check("register as ADMIN rejected", "POST", "/auth/register", {"email": "new@example.com", "password": "Passw0rd!", "role": "ADMIN"}, status=400, code="VALIDATION_ERROR")

# ------------------------------------------------------------ products
cat = f"cat-{RUN}"
p = {"name": "Phone", "description": "Smartphone", "price": 100.00, "stock": 10, "category": cat, "status": "ACTIVE"}
phone = check("seller creates product", "POST", "/products", p, seller, status=201)
assert phone["price"] == 100.0 and phone["created_at"]
laptop = check("seller creates product 2", "POST", "/products", {**p, "name": "Laptop", "price": 1000, "stock": 2}, seller, status=201)
inactive = check("seller creates inactive", "POST", "/products", {**p, "name": "Old", "status": "INACTIVE"}, seller, status=201)
other = check("seller2 creates product", "POST", "/products", {**p, "name": "Other"}, seller2, status=201)

bad = check("validation: many errors", "POST", "/products",
            {"name": "", "price": 0, "stock": -1, "category": "", "status": "NOPE"}, seller, status=400, code="VALIDATION_ERROR")
assert len(bad["details"]["errors"]) >= 5, bad
check("validation: price 3 decimals", "POST", "/products", {**p, "price": 1.005}, seller, status=400, code="VALIDATION_ERROR")
check("validation: name too long", "POST", "/products", {**p, "name": "x" * 256}, seller, status=400, code="VALIDATION_ERROR")
check("validation: description 4001", "POST", "/products", {**p, "description": "x" * 4001}, seller, status=400, code="VALIDATION_ERROR")
check("validation: missing field", "POST", "/products", {"name": "a"}, seller, status=400, code="VALIDATION_ERROR")
check("validation: unknown field", "POST", "/products", {**p, "hack": 1}, seller, status=400, code="VALIDATION_ERROR")
check("validation: bad uuid", "GET", "/products/not-a-uuid", token=seller, status=400, code="VALIDATION_ERROR")
check("validation: size>100", "GET", "/products?size=101", token=seller, status=400, code="VALIDATION_ERROR")

page = check("list filtered", "GET", f"/products?category={cat}&status=ACTIVE&page=0&size=2", token=seller)
assert page["totalElements"] == 3 and len(page["items"]) == 2 and page["size"] == 2, page
page = check("list page 1", "GET", f"/products?category={cat}&status=ACTIVE&page=1&size=2", token=seller)
assert len(page["items"]) == 1, page
check("get product", "GET", f"/products/{phone['id']}", token=seller)
check("product not found", "GET", f"/products/{uuid.uuid4()}", token=seller, status=404, code="PRODUCT_NOT_FOUND")

upd = check("partial update", "PUT", f"/products/{phone['id']}", {"description": None}, seller)
assert upd["description"] is None and upd["name"] == "Phone" and upd["updated_at"] > upd["created_at"]
check("update empty body", "PUT", f"/products/{phone['id']}", {}, seller, status=400, code="VALIDATION_ERROR")
check("update null name", "PUT", f"/products/{phone['id']}", {"name": None}, seller, status=400, code="VALIDATION_ERROR")
check("seller updates foreign product", "PUT", f"/products/{other['id']}", {"stock": 1}, seller, status=403, code="ACCESS_DENIED")
check("admin updates any product", "PUT", f"/products/{other['id']}", {"stock": 5}, admin)
arch = check("soft delete", "DELETE", f"/products/{other['id']}", token=seller2)
assert arch["status"] == "ARCHIVED"
check("archived still readable", "GET", f"/products/{other['id']}", token=seller)

# ---------------------------------------------------------- promo codes
now = datetime.now(timezone.utc)
promo = lambda code, **kw: {"code": code, "discount_type": "PERCENTAGE", "discount_value": 10, "min_order_amount": 0,
                            "max_uses": 100, "valid_from": (now - timedelta(days=1)).isoformat(),
                            "valid_until": (now + timedelta(days=1)).isoformat(), **kw}
check("promo 10%", "POST", "/promo-codes", promo(f"P10_{RUN}"), seller, status=201)
check("promo duplicate", "POST", "/promo-codes", promo(f"P10_{RUN}"), seller, status=409, code="PROMO_CODE_ALREADY_EXISTS")
check("promo 80% (>70 -> no discount)", "POST", "/promo-codes", promo(f"P80_{RUN}", discount_value=80), admin, status=201)
check("promo fixed min 500", "POST", "/promo-codes", promo(f"FIX_{RUN}", discount_type="FIXED_AMOUNT", discount_value=50, min_order_amount=500), admin, status=201)
check("promo expired", "POST", "/promo-codes", promo(f"EXP_{RUN}", valid_until=(now - timedelta(hours=1)).isoformat(),
      valid_from=(now - timedelta(days=2)).isoformat()), admin, status=201)
check("promo single use", "POST", "/promo-codes", promo(f"ONE_{RUN}", max_uses=1), admin, status=201)
check("promo bad pattern", "POST", "/promo-codes", promo("bad-code"), admin, status=400, code="VALIDATION_ERROR")

# --------------------------------------------------------------- orders
buyer = user()["access_token"]
check("user cannot create product", "POST", "/products", p, buyer, status=403, code="ACCESS_DENIED")
check("user cannot create promo", "POST", "/promo-codes", promo(f"U_{RUN}"), buyer, status=403, code="ACCESS_DENIED")
check("seller cannot order", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, seller, status=403, code="ACCESS_DENIED")

check("order: 0 items", "POST", "/orders", {"items": []}, buyer, status=400, code="VALIDATION_ERROR")
check("order: 51 items", "POST", "/orders", {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}] * 51}, buyer, status=400, code="VALIDATION_ERROR")
check("order: quantity 1000", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1000}]}, buyer, status=400, code="VALIDATION_ERROR")
check("order: quantity 0", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 0}]}, buyer, status=400, code="VALIDATION_ERROR")
check("order: promo lowercase", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": "abc_def"}, buyer, status=400, code="VALIDATION_ERROR")
check("order: duplicate products", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}] * 2}, buyer, status=400, code="VALIDATION_ERROR")
check("order: product not found", "POST", "/orders", {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}]}, buyer, status=404, code="PRODUCT_NOT_FOUND")
check("order: product inactive", "POST", "/orders", {"items": [{"product_id": inactive["id"], "quantity": 1}]}, buyer, status=409, code="PRODUCT_INACTIVE")
check("order: product archived", "POST", "/orders", {"items": [{"product_id": other["id"], "quantity": 1}]}, buyer, status=409, code="PRODUCT_INACTIVE")
st = check("order: insufficient stock", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 11}, {"product_id": laptop["id"], "quantity": 3}]},
           buyer, status=409, code="INSUFFICIENT_STOCK")
assert len(st["details"]["items"]) == 2 and st["details"]["items"][0]["available"] == 10, st
check("order: promo not found", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": "NOPE_NOPE"}, buyer, status=422, code="PROMO_CODE_INVALID")
check("order: promo expired", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": f"EXP_{RUN}"}, buyer, status=422, code="PROMO_CODE_INVALID")
check("order: promo min amount", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": f"FIX_{RUN}"}, buyer, status=422, code="PROMO_CODE_MIN_AMOUNT")
assert check("stock untouched after failures", "GET", f"/products/{phone['id']}", token=buyer)["stock"] == 10

order = check("order: create with 10% promo", "POST", "/orders",
              {"items": [{"product_id": phone["id"], "quantity": 2}, {"product_id": laptop["id"], "quantity": 1}], "promo_code": f"P10_{RUN}"},
              buyer, status=201)
assert order["status"] == "CREATED" and order["total_amount"] == 1080 and order["discount_amount"] == 120, order
assert check("stock reserved", "GET", f"/products/{phone['id']}", token=buyer)["stock"] == 8
check("order: rate limit", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer, status=429, code="ORDER_LIMIT_EXCEEDED")

check("price change", "PUT", f"/products/{phone['id']}", {"price": 999}, seller)
again = check("price snapshot kept", "GET", f"/orders/{order['id']}", token=buyer)
assert again["total_amount"] == 1080, again

buyer2 = user()["access_token"]
check("foreign order read", "GET", f"/orders/{order['id']}", token=buyer2, status=403, code="ORDER_OWNERSHIP_VIOLATION")
check("foreign order update", "PUT", f"/orders/{order['id']}", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer2, status=403, code="ORDER_OWNERSHIP_VIOLATION")
check("foreign order cancel", "POST", f"/orders/{order['id']}/cancel", token=buyer2, status=403, code="ORDER_OWNERSHIP_VIOLATION")
check("seller reads order", "GET", f"/orders/{order['id']}", token=seller, status=403, code="ACCESS_DENIED")
check("order not found", "GET", f"/orders/{uuid.uuid4()}", token=buyer, status=404, code="ORDER_NOT_FOUND")
check("admin reads any order", "GET", f"/orders/{order['id']}", token=admin)

# update: 1 phone at the new price 999 -> 999 < no min, 10% -> 899.10
upd = check("order: update", "PUT", f"/orders/{order['id']}", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer)
assert upd["total_amount"] == 899.10 and upd["discount_amount"] == 99.90 and upd["items"][0]["price_at_order"] == 999, upd
assert check("old stock returned", "GET", f"/products/{laptop['id']}", token=buyer)["stock"] == 2
check("order: update rate limit", "PUT", f"/orders/{order['id']}", {"items": [{"product_id": phone["id"], "quantity": 2}]}, buyer, status=429, code="ORDER_LIMIT_EXCEEDED")

check("user cannot pay", "POST", f"/orders/{order['id']}/status", {"status": "PAID"}, buyer, status=409, code="INVALID_STATE_TRANSITION")
pend = check("user -> PAYMENT_PENDING", "POST", f"/orders/{order['id']}/status", {"status": "PAYMENT_PENDING"}, buyer)
assert pend["status"] == "PAYMENT_PENDING"
check("update in PAYMENT_PENDING", "PUT", f"/orders/{order['id']}", {"items": [{"product_id": phone["id"], "quantity": 1}]}, admin, status=409, code="INVALID_STATE_TRANSITION")
check("user -> PAID denied", "POST", f"/orders/{order['id']}/status", {"status": "PAID"}, buyer, status=403, code="ACCESS_DENIED")
check("admin -> PAID", "POST", f"/orders/{order['id']}/status", {"status": "PAID"}, admin)
check("cancel PAID", "POST", f"/orders/{order['id']}/cancel", token=buyer, status=409, code="INVALID_STATE_TRANSITION")
check("PAID -> COMPLETED skip", "POST", f"/orders/{order['id']}/status", {"status": "COMPLETED"}, admin, status=409, code="INVALID_STATE_TRANSITION")
check("admin -> SHIPPED", "POST", f"/orders/{order['id']}/status", {"status": "SHIPPED"}, admin)
check("admin -> COMPLETED", "POST", f"/orders/{order['id']}/status", {"status": "COMPLETED"}, admin)

# cancel flow with promo returned
buyer3 = user()["access_token"]
o3 = check("order with single-use promo", "POST", "/orders", {"items": [{"product_id": laptop["id"], "quantity": 2}], "promo_code": f"ONE_{RUN}"}, buyer3, status=201)
assert check("laptop stock 0", "GET", f"/products/{laptop['id']}", token=buyer3)["stock"] == 0
check("single-use promo exhausted", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": f"ONE_{RUN}"}, buyer2, status=422, code="PROMO_CODE_INVALID")
check("rate limit is checked before active order", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer3, status=429, code="ORDER_LIMIT_EXCEEDED")
c = check("cancel", "POST", f"/orders/{o3['id']}/cancel", token=buyer3)
assert c["status"] == "CANCELED"
assert check("stock returned on cancel", "GET", f"/products/{laptop['id']}", token=buyer3)["stock"] == 2
check("promo use returned on cancel", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": f"ONE_{RUN}"}, buyer2, status=201)
check("cancel twice", "POST", f"/orders/{o3['id']}/cancel", token=buyer3, status=409, code="INVALID_STATE_TRANSITION")
# the seeded admin is shared between runs: a re-run within the rate-limit window gets 429
ao = check("admin can order", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, admin, status=(201, 429))
check("admin is rate limited too", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, admin, status=429, code="ORDER_LIMIT_EXCEEDED")
if "id" in ao:
    check("admin cancels own order", "POST", f"/orders/{ao['id']}/cancel", token=admin)

# >70% promo -> full price
buyer4 = user()["access_token"]
o4 = check("80% promo gives no discount", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": f"P80_{RUN}"}, buyer4, status=201)
assert o4["discount_amount"] == 0 and o4["total_amount"] == 999, o4

# promo dropped on update below min amount
buyer5 = user()["access_token"]
o5 = check("fixed promo order", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}], "promo_code": f"FIX_{RUN}"}, buyer5, status=201)
assert o5["total_amount"] == 949 and o5["discount_amount"] == 50, o5
check("make phone cheap", "PUT", f"/products/{phone['id']}", {"price": 100}, admin)
o5u = check("update below min -> promo removed", "PUT", f"/orders/{o5['id']}", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer5)
assert o5u["promo_code"] is None and o5u["discount_amount"] == 0 and o5u["total_amount"] == 100, o5u

if WAIT:
    print("waiting 61s for the rate-limit window...")
    time.sleep(61)
    check("ORDER_HAS_ACTIVE", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer4, status=409, code="ORDER_HAS_ACTIVE")
    check("second update after window", "PUT", f"/orders/{o5['id']}", {"items": [{"product_id": phone["id"], "quantity": 3}]}, buyer5)
    check("new order after previous completed", "POST", "/orders", {"items": [{"product_id": phone["id"], "quantity": 1}]}, buyer, status=201)

check("unknown route", "GET", "/nope", token=admin, status=404, code="NOT_FOUND")
print(f"\nAll {passed} checks passed")

"""Run once against a fresh demo database. This test makes real stock bookings."""

from concurrent.futures import ThreadPoolExecutor
import json
import sys
from threading import Barrier
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, data=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = Request(base + path, data=body, headers={"Content-Type": "application/json"})
    try:
        reply = urlopen(req, timeout=15)
    except HTTPError as error:
        reply = error
    with reply:
        return reply.status, json.load(reply)


def main(primary, secondary):
    status, rows = request(primary, "/products")
    assert status == 200
    product = next(p for p in rows if p["sku"] == "EL-1040")
    assert product["stock"] == 5, "Start with the original demo stock of five."
    path = f"/products/{product['id']}/movements"
    _, initial = request(primary, path)
    barrier = Barrier(2, timeout=5)
    payloads = [
        {"request_id": str(uuid4()), "delta": -4, "note": "Prüfung A"},
        {"request_id": str(uuid4()), "delta": -3, "note": "Prüfung B"},
    ]

    def send(base, payload):
        barrier.wait()
        return request(base, path, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, primary, payloads[0])
        second = pool.submit(send, secondary, payloads[1])
        results = [first.result(), second.result()]

    assert sorted(code for code, _ in results) == [201, 409], results
    winner = next(i for i, (code, _) in enumerate(results) if code == 201)
    booking = results[winner][1]
    available = 5 + payloads[winner]["delta"]
    assert results[1 - winner][1] == {"error": "insufficient_stock", "stock": available}
    assert booking["stock_after"] == available
    assert request(primary, path, payloads[winner]) == (200, booking)
    changed = dict(payloads[winner], note="Andere Nutzdaten")
    assert request(primary, path, changed) == (409, {"error": "request_id_conflict"})
    invalid = dict(payloads[winner], delta=0)
    assert request(primary, path, invalid) == (422, {"error": "invalid_movement"})
    assert request(primary, "/products/2147483647/movements")[0] == 404
    assert request(primary, path)[1] == [booking, *initial]
    assert next(p for p in request(secondary, "/products")[1] if p["id"] == product["id"])["stock"] == available
    print(f"HTTP {results[0][0]} / {results[1][0]} · stock {available} · one movement · replay and validation OK")


if __name__ == "__main__":
    hosts = [arg.rstrip("/") for arg in sys.argv[1:]] or ["http://127.0.0.1:8077"]
    main(hosts[0], hosts[-1])

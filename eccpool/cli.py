"""Command line: serve, admin (over HTTP), worker, curve generation."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import httpx


def _admin(args) -> httpx.Client:
    token = os.environ.get("ECCPOOL_ADMIN_TOKEN")
    if not token:
        raise SystemExit("set ECCPOOL_ADMIN_TOKEN")
    return httpx.Client(base_url=args.url.rstrip("/"), timeout=60,
                        headers={"Authorization": f"Bearer {token}"})


def _out(r: httpx.Response) -> None:
    try:
        body = r.json()
    except ValueError:
        body = r.text
    print(json.dumps(body, indent=2))
    if r.status_code >= 400:
        sys.exit(1)


def cmd_serve(args) -> None:
    import uvicorn

    from .server import ServerConfig, create_app
    logging.basicConfig(level=logging.INFO)
    cfg = ServerConfig.from_env()
    if args.db:
        cfg.db_path = args.db
    # One process: the rate limiter and the SQLite writer live in it.
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, workers=1,
                proxy_headers=cfg.trust_proxy, log_level="info")


def cmd_key(args) -> None:
    c = _admin(args)
    if args.action == "issue":
        body = {"label": args.label, "contact": args.contact or ""}
        for k in ("shares_per_minute", "share_burst", "requests_per_minute"):
            if getattr(args, k) is not None:
                body[k] = getattr(args, k)
        _out(c.post("/admin/keys", json=body))
    elif args.action == "list":
        _out(c.get("/admin/keys", params={"status": args.status} if args.status else None))
    elif args.action == "show":
        _out(c.get(f"/admin/keys/{args.key_id}"))
    elif args.action in ("revoke", "suspend", "reinstate"):
        params = {"forfeit": "true"} if args.action == "revoke" and args.forfeit else None
        _out(c.post(f"/admin/keys/{args.key_id}/{args.action}", json={"reason": args.reason},
                    params=params))
    elif args.action == "limits":
        body = {k: getattr(args, k) for k in ("shares_per_minute", "share_burst",
                                               "requests_per_minute")}
        _out(c.patch(f"/admin/keys/{args.key_id}/limits", json=body))
    elif args.action == "audit":
        _out(c.post(f"/admin/keys/{args.key_id}/audit", json={"count": args.count, "kind": args.kind}))


def cmd_job(args) -> None:
    c = _admin(args)
    if args.action == "create":
        with open(args.challenge) as f:
            ch = json.load(f)
        body = {"name": args.name or ch.get("name", args.challenge), "dp_bits": args.dp_bits,
                "curve": ch["curve"], "r": args.r,
                "checkpoint_interval": args.checkpoint_interval,
                "verification": args.verification, "challenge_rate": args.challenge_rate}
        if "Q" in ch:
            body["Q"] = ch["Q"]
        else:
            body["seed"] = ch["seed"]
        _out(c.post("/admin/jobs", json=body))
    elif args.action in ("pause", "resume"):
        _out(c.post(f"/admin/jobs/{args.job_id}/status",
                    json={"status": "paused" if args.action == "pause" else "active"}))


def cmd_log(args) -> None:
    c = _admin(args)
    if args.what == "events":
        _out(c.get("/admin/events", params={k: v for k, v in
                                           (("key_id", args.key_id), ("limit", args.limit)) if v}))
    elif args.what == "audits":
        _out(c.get("/admin/audits", params={k: v for k, v in
                                           (("status", args.status), ("limit", args.limit)) if v}))
    else:
        _out(c.get("/admin/collisions"))


def cmd_worker(args) -> None:
    from .worker import Client, work
    logging.basicConfig(level=logging.INFO)
    key = os.environ.get("ECCPOOL_API_KEY")
    if not key:
        raise SystemExit("set ECCPOOL_API_KEY")
    client = Client(args.url, key)
    job_id = args.job
    if job_id is None:
        active = [j for j in client.jobs() if j["status"] == "active"]
        if not active:
            raise SystemExit("no active jobs")
        job_id = active[0]["job_id"]
    final = work(client, job_id, procs=args.procs, batch=args.batch, max_shares=args.max_shares)
    print(json.dumps({"job": {k: final.get(k) for k in ("job_id", "status", "solution")},
                      "me": client.me()}, indent=2))


def cmd_gen(args) -> None:
    from .curvegen import (generate_goldilocks_curve, generate_prime_order_curve, make_challenge,
                           make_subgroup_challenge)
    if args.field == "goldilocks":
        curve, h = generate_goldilocks_curve(args.bits)
        ch = make_subgroup_challenge(curve, h, args.seed)
    else:
        ch = make_challenge(generate_prime_order_curve(args.bits), args.seed)
    ch["name"] = args.name or f"ecc{args.bits}-{args.seed}"
    print(json.dumps(ch, indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="eccpool")
    p.add_argument("--url", default=os.environ.get("ECCPOOL_URL", "http://127.0.0.1:8000"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--db")
    s.set_defaults(fn=cmd_serve)

    k = sub.add_parser("key", help="manage API keys")
    k.add_argument("action", choices=["issue", "list", "show", "revoke", "suspend", "reinstate",
                                      "limits", "audit"])
    k.add_argument("key_id", nargs="?")
    k.add_argument("--label")
    k.add_argument("--contact")
    k.add_argument("--reason", default="")
    k.add_argument("--forfeit", action="store_true", help="revoke and forfeit all credit")
    k.add_argument("--status")
    k.add_argument("--shares-per-minute", type=float)
    k.add_argument("--share-burst", type=int)
    k.add_argument("--requests-per-minute", type=float)
    k.add_argument("--count", type=int, default=10)
    k.add_argument("--kind", default="segment", choices=["segment", "full"])
    k.set_defaults(fn=cmd_key)

    j = sub.add_parser("job", help="manage jobs")
    j.add_argument("action", choices=["create", "pause", "resume"])
    j.add_argument("job_id", nargs="?")
    j.add_argument("--challenge", help="challenge JSON (from gen-curve or challenges/)")
    j.add_argument("--name")
    j.add_argument("--dp-bits", type=int, default=12)
    j.add_argument("--r", type=int, default=32)
    j.add_argument("--checkpoint-interval", type=int, default=0)
    j.add_argument("--verification", default="audit", choices=["audit", "stark", "stark-challenge"],
                   help="audit: server replays random segments; stark: every share carries a "
                        "STARK proof of its walk; stark-challenge: random segments are proven "
                        "on demand")
    j.add_argument("--challenge-rate", type=float, default=0.25,
                   help="stark-challenge: probability a share is challenged")
    j.set_defaults(fn=cmd_job)

    lg = sub.add_parser("log", help="read events, audits or collisions")
    lg.add_argument("what", choices=["events", "audits", "collisions"])
    lg.add_argument("--key-id")
    lg.add_argument("--status")
    lg.add_argument("--limit", type=int)
    lg.set_defaults(fn=cmd_log)

    w = sub.add_parser("worker", help="run the reference worker")
    w.add_argument("--job")
    w.add_argument("--procs", type=int, default=os.cpu_count() or 1)
    w.add_argument("--batch", type=int, default=16)
    w.add_argument("--max-shares", type=int)
    w.set_defaults(fn=cmd_worker)

    g = sub.add_parser("gen-curve", help="generate a prime-order curve challenge")
    g.add_argument("--bits", type=int, required=True)
    g.add_argument("--seed", required=True, help="public seed G and Q are hashed from")
    g.add_argument("--name")
    g.add_argument("--field", choices=["random", "goldilocks"], default="random",
                   help="goldilocks: curve over the STARK field with a prime subgroup of --bits "
                        "bits (needed for stark verification)")
    g.set_defaults(fn=cmd_gen)

    args = p.parse_args(argv)
    if args.cmd == "key" and args.action == "issue" and not args.label:
        p.error("key issue needs --label")
    if args.cmd == "key" and args.action not in ("issue", "list") and not args.key_id:
        p.error(f"key {args.action} needs a key_id")
    if args.cmd == "key" and args.action in ("revoke", "suspend", "reinstate") and not args.reason:
        p.error(f"key {args.action} needs --reason")
    if args.cmd == "job" and args.action == "create" and not args.challenge:
        p.error("job create needs --challenge")
    args.fn(args)


if __name__ == "__main__":
    main()

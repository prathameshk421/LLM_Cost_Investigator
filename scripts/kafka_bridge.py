#!/usr/bin/env python3
"""Optional Kafka adapter: read JSON events from a topic and POST them to /v1/ingest.

Needs confluent-kafka (preferred) or aiokafka:
    pip install confluent-kafka
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request


def post_with_retry(url: str, events: list[dict], token: str | None, retries: int = 8) -> bool:
    body = json.dumps({"events": events}).encode()
    delay = 0.5
    for _ in range(retries):
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 502, 503, 504):
                print(f"ingest rejected batch: HTTP {exc.code}", file=sys.stderr)
                return False
        except OSError as exc:
            print(f"ingest unreachable: {exc}", file=sys.stderr)
        time.sleep(delay)
        delay = min(delay * 2, 10)
    return False


def parse(raw: bytes | str) -> dict | None:
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def run_confluent(args, Consumer) -> None:
    c = Consumer({"bootstrap.servers": args.bootstrap, "group.id": args.group, "auto.offset.reset": "latest",
                  "enable.auto.commit": False})
    c.subscribe([args.topic])
    batch: list[dict] = []
    last = time.time()
    try:
        while True:
            msg = c.poll(0.5)
            if msg is not None and not msg.error():
                ev = parse(msg.value())
                if ev:
                    batch.append(ev)
            if batch and (len(batch) >= args.batch_size or time.time() - last >= args.flush_s):
                if post_with_retry(args.url, batch, args.token):
                    c.commit(asynchronous=False)
                batch, last = [], time.time()
            elif not batch:
                last = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        c.close()


async def run_aiokafka(args, AIOKafkaConsumer) -> None:
    consumer = AIOKafkaConsumer(args.topic, bootstrap_servers=args.bootstrap, group_id=args.group,
                                auto_offset_reset="latest")
    await consumer.start()
    try:
        while True:
            data = await consumer.getmany(timeout_ms=int(args.flush_s * 1000), max_records=args.batch_size)
            batch = [e for msgs in data.values() for m in msgs if (e := parse(m.value))]
            if batch:
                await asyncio.to_thread(post_with_retry, args.url, batch, args.token)
    finally:
        await consumer.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--group", default="llmci-bridge")
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/ingest")
    ap.add_argument("--token", default=None)
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--flush-s", type=float, default=1.0)
    args = ap.parse_args()

    try:
        from confluent_kafka import Consumer  # type: ignore

        run_confluent(args, Consumer)
        return
    except ImportError:
        pass
    try:
        from aiokafka import AIOKafkaConsumer  # type: ignore

        try:
            asyncio.run(run_aiokafka(args, AIOKafkaConsumer))
        except KeyboardInterrupt:
            pass
        return
    except ImportError:
        pass
    print("No Kafka client installed. Run: pip install confluent-kafka   (or: pip install aiokafka)", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()

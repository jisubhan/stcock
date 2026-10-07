"""Bounded official OAuth and read-only market-data verification.

Run separately from the API: each new client-credentials token invalidates
the preceding token. No credential or token is printed or written to disk.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brokers.toss.documented import OfficialTossClient, TossAPIError, run_probe, save_probe


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='Explicitly run bounded official OAuth/read-only samples; stop the API first.')
    args = parser.parse_args()
    if not args.probe or os.environ.get('TOSS_DATA_MODE', 'auto').lower() == 'demo':
        from app.market.ingestion import IngestionService
        print(json.dumps({'readiness': IngestionService().status(), 'live_probes_requested': False}, ensure_ascii=False, indent=2))
        return 2
    client = None
    try:
        client = OfficialTossClient(max_http_calls=12, probe_retry_budget=1)
        report = await run_probe(client)
        save_probe(report)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.authentication.get("status") == "passed" else 1
    except TossAPIError as error:
        print(json.dumps(error.safe_dict(), ensure_ascii=False))
        return 1
    finally:
        if client is not None:
            await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

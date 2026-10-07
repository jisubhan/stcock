#!/usr/bin/env python3
"""Show persisted safe diagnostics; --live explicitly retries read-only ingestion."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.market.ingestion import IngestionService


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='After fixing external settings, retry read-only OAuth/ingestion; stop the API first.')
    args = parser.parse_args()
    service = IngestionService()
    try:
        if args.live:
            await service.refresh(explicit=True)
        status = service.status()
        print(json.dumps({'readiness': status, 'live_probes_requested': args.live}, ensure_ascii=False, indent=2))
        return 0 if status['ready'] else 2
    finally:
        await service.close()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))

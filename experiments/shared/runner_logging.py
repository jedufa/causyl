"""Small, engine-independent JSONL event logger for manifest execution."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


def create_logger(report_dir: str, *, level: str = 'INFO') -> logging.Logger:
    path = Path(report_dir) / 'logs' / 'runner.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.Logger('causyl.runner.manifest')
    logger.setLevel(logging.DEBUG if level.upper() == 'TRACE' else level.upper())
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, *, event: str, level: str = 'INFO', **fields: object) -> None:
    payload = {'utc': datetime.now(timezone.utc).isoformat(), 'event': event, 'level': level, **fields}
    logger.log(getattr(logging, level.upper(), logging.INFO), json.dumps(payload, default=str))

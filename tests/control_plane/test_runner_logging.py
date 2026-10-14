import json

from experiments.shared.runner_logging import create_logger, log_event


def test_runner_records_events_without_engine(tmp_path):
    logger = create_logger(str(tmp_path))
    try:
        log_event(logger, event='EXECUTION_PLAN', computed_concurrency=2)
        log_event(logger, event='SUPPRESSED', level='DEBUG')
        rows = [json.loads(line) for line in (tmp_path / 'logs/runner.jsonl').read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]['event'] == 'EXECUTION_PLAN'
        assert rows[0]['computed_concurrency'] == 2
        assert rows[0]['utc']
    finally:
        for handler in logger.handlers:
            handler.close()

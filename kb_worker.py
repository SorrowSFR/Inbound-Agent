import asyncio
import logging
import os
import sys
from pathlib import Path


def _bootstrap_venv() -> None:
    project_dir = Path(__file__).resolve().parent
    venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return
    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except Exception:
        return
    if current_python == target_python:
        return
    os.execv(str(target_python), [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_bootstrap_venv()

from dotenv import load_dotenv

import kb
from backend_config import read_config

load_dotenv()
logging.basicConfig(level=logging.INFO)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
logger = logging.getLogger("kb-worker")
async def main() -> None:
    logger.info("[KB] Knowledge base worker started.")
    while True:
        config = read_config()
        runtime = kb.get_runtime_config(config)
        poll_seconds = runtime["kb_worker_poll_seconds"]
        try:
            queued = kb.maybe_sync_leadrat(config)
            if queued:
                logger.info(f"[KB] Scheduled Lead RRat sync job {queued.get('id')}")
        except Exception as exc:
            logger.error(f"[KB] Lead RRat schedule check failed: {exc}")
        try:
            processed = kb.process_pending_jobs(config, limit=5)
            if processed:
                logger.info(f"[KB] Processed {len(processed)} KB jobs")
        except Exception as exc:
            logger.error(f"[KB] KB job processing failed: {exc}")
        await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())

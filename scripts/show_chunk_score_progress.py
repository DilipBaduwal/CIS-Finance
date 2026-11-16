"""Show live progress for the resumable benchmark chunk-scoring job."""
import json
import os
import time
from pathlib import Path
import sys


CHECKPOINT = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores.checkpoint.json")
SOURCE = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_all_chunks.json")
FULL_RUN = os.environ.get("FULL_CHUNK_SCORING", "0") == "1" or (len(sys.argv) > 1 and sys.argv[1].lower() == "full")
TOTAL = sum(
    len(theme.get("chunk_ids", [])) if FULL_RUN else min(len(theme.get("chunk_ids", [])), 20)
    for theme in json.loads(SOURCE.read_text(encoding="utf-8")).get("themes", [])
)
CHECKPOINT = Path(
    "artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores.checkpoint.json"
    if FULL_RUN
    else (sys.argv[1]
    if len(sys.argv) > 1
    else "artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores_sampled.checkpoint.json"
    )
)


def main():
    print("Watching {}".format(CHECKPOINT))
    print("Press Ctrl+C to stop watching.\n")
    try:
        while True:
            completed = 0
            if CHECKPOINT.exists():
                try:
                    data = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
                    completed = len(data.get("scores", {}))
                except (OSError, ValueError):
                    pass
            remaining = max(TOTAL - completed, 0)
            percent = completed * 100.0 / TOTAL
            print(
                "\rScored: {:4d}/{:4d} ({:6.2f}%) | Remaining: {:4d}".format(
                    completed, TOTAL, percent, remaining
                ),
                end="",
                flush=True,
            )
            if completed >= TOTAL:
                print("\nScoring complete.")
                return
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopped watching.")


if __name__ == "__main__":
    main()

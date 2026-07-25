"""Test-only faultable worker runner for Phase 2 integration experiments."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Faultable AI Worker runner (tests only)")
    parser.add_argument("--fail-result-once", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("AI_WORKER_TRANSPORT_SHELL", "true")
    if args.fail_result_once:
        os.environ["AI_WORKER_TEST_FAIL_RESULT_ONCE"] = "1"
    # Production entry remains celery CLI; this module is documentation/harness only.
    print("faultable worker helper ready; start celery with AI_WORKER_TRANSPORT_SHELL=true")


if __name__ == "__main__":
    main()

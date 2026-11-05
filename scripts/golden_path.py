"""Golden path: send every demo example to a running Prove It API and check the demo story.

    python scripts/golden_path.py                         # local API on port 8000
    python scripts/golden_path.py --base https://<domain>  # deployed app

Standard library only, so it runs from any machine. Exit code 0 only when every check passes.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CONTAINED = {"write_blocked", "network_blocked", "process_limit", "secret_absent"}


def review(base: str, code: str, intent: str) -> dict:
    """POST /api/review and return the final `report` event of the SSE stream."""
    request = urllib.request.Request(
        f"{base}/api/review",
        data=json.dumps({"code": code, "intent": intent or None}).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    event, data = "message", []
    # The server pings every 15 s, so a per-read timeout of 120 s only trips on a dead stream.
    with urllib.request.urlopen(request, timeout=120) as response:
        for raw in response:
            line = raw.decode().rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            elif not line:
                if event == "report" and data:
                    return json.loads("\n".join(data))
                event, data = "message", []
    raise RuntimeError("stream ended without a report")


def post_json(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def check_signature(base: str, report: dict) -> list[str]:
    """The receipt verifies as sent, and stops verifying once its verdict is flipped."""
    receipt = report["receipt"]
    if receipt is None:
        return ["no receipt"]
    problems = []
    if not post_json(base, "/api/verify", receipt)["valid"]:
        problems.append("receipt signature does not verify")
    forged = {**receipt, "verdict": "PASS" if receipt["verdict"] == "FAIL" else "FAIL"}
    if post_json(base, "/api/verify", forged)["valid"]:
        problems.append("a forged receipt verified")
    return problems


def check_installments(report: dict) -> list[str]:
    problems = []
    if report["verdict"] != "FAIL":
        problems.append(f"verdict {report['verdict']}, expected FAIL")
    if (report["fix_run"] or {}).get("exit_code") != 0:
        problems.append("proposed fix did not pass the frozen tests")
    if not report["mutation"] or report["mutation"]["killed"] < 1:
        problems.append("no sabotage caught")
    return problems


def check_hostile(report: dict) -> list[str]:
    problems = []
    if report["verdict"] != "FAIL":
        problems.append(f"verdict {report['verdict']}, expected FAIL despite the prompt injection")
    if missing := CONTAINED - {event["kind"] for event in report["events"]}:
        problems.append(f"containment evidence missing: {', '.join(sorted(missing))}")
    if report["fix"] is not None:
        problems.append("a fix was attempted for contained code")
    return problems


def check_loop(report: dict) -> list[str]:
    if "timeout" not in {event["kind"] for event in report["events"]}:
        return [f"no timeout event (verdict {report['verdict']})"]
    return []


CHECKS: dict[str, Callable[[dict], list[str]]] = {
    "parcelamento": check_installments,
    "hostile": check_hostile,
    "hostile_loop": check_loop,
}


def leftover_containers() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=proveit-", "-q"],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.split()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="app URL, no trailing slash")
    base = parser.parse_args().base.rstrip("/")

    failed = False
    for example in json.loads((EXAMPLES / "index.json").read_text()):
        name = example["id"]
        start = time.monotonic()
        report = None
        try:
            report = review(base, (EXAMPLES / f"{name}.py").read_text(), example["intent"])
            problems = CHECKS[name](report) + check_signature(base, report)
            if report["error"]:
                problems.append(f"error: {report['error']}")
        except urllib.error.HTTPError as exc:
            problems = [f"HTTP {exc.code}: {exc.read().decode(errors='replace')}"]
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            problems = [f"request failed: {exc}"]
        took = time.monotonic() - start
        failed |= bool(problems)
        print(f"{'FAIL' if problems else 'ok  '}  {name:<13} {took:5.1f}s  {'; '.join(problems)}")
        if problems and report is not None:
            dump = Path(tempfile.gettempdir()) / f"golden_path_{name}_{int(time.time())}.json"
            dump.write_text(json.dumps(report, indent=1))
            print(f"      full report: {dump}")

    if urlparse(base).hostname in ("127.0.0.1", "localhost"):
        leftovers = leftover_containers()
        failed |= bool(leftovers)
        print(f"{'FAIL' if leftovers else 'ok  '}  no sandbox containers left ({len(leftovers)} found)")
    else:
        print("skip  container check (only possible on the machine running Docker)")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

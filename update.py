"""Update the bot to the latest code, safely.

Run it directly (`python update.py`) or from option 5 of start.bat.

What it does, in order:
  1. Checks this is a git checkout and reports where you are.
  2. Refuses to run if you have uncommitted edits, so an update can never
     silently discard your own changes - it tells you how to keep them.
  3. Fetches and fast-forwards the current branch, retrying on network
     errors with backoff.
  4. Reinstalls dependencies only if requirements.txt actually changed.
  5. Runs the test suite, so you learn about a broken update here rather
     than in front of a live chart.

It never force-pushes, never resets, and never rewrites history. If the
branch cannot fast-forward it stops and explains, leaving your repository
exactly as it was.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NETWORK_RETRY_DELAYS = (2, 4, 8, 16)


def run(args: list[str], capture: bool = True, check: bool = False) -> tuple[int, str]:
    """Run a command in the repo root. Returns (exit code, combined output)."""
    proc = subprocess.run(
        args, cwd=ROOT, text=True, check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    out = (proc.stdout or "").strip() if capture else ""
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}\n{out}")
    return proc.returncode, out


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")


def git_available() -> bool:
    return run(["git", "--version"])[0] == 0


def ensure_repo() -> None:
    code, _ = run(["git", "rev-parse", "--git-dir"])
    if code != 0:
        raise SystemExit(
            "This folder is not a git checkout, so there is nothing to update.\n"
            "If you downloaded a ZIP, re-download it or clone with git instead."
        )


def current_branch() -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])[1]


def short_head() -> str:
    return run(["git", "log", "-1", "--pretty=%h %s"])[1]


def working_tree_dirty() -> list[str]:
    _, out = run(["git", "status", "--porcelain"])
    return [ln for ln in out.splitlines() if ln.strip()]


def file_hash(rel: str) -> str | None:
    code, out = run(["git", "hash-object", rel])
    return out if code == 0 else None


def fetch_with_retries(branch: str) -> None:
    """Network hiccups are common; a failed fetch is not a failed update."""
    last = ""
    for attempt, delay in enumerate((0, *NETWORK_RETRY_DELAYS), start=1):
        if delay:
            say(f"      network problem, retrying in {delay}s "
                f"(attempt {attempt}/{len(NETWORK_RETRY_DELAYS) + 1})…")
            time.sleep(delay)
        code, out = run(["git", "fetch", "origin", branch])
        if code == 0:
            return
        last = out
    raise SystemExit(
        "Could not reach the server after several attempts.\n"
        "Check your internet connection and try again.\n\n"
        f"Last error from git:\n{last}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-tests", action="store_true",
                   help="do not run the test suite after updating")
    p.add_argument("--stash", action="store_true",
                   help="set your local edits aside instead of stopping")
    args = p.parse_args()

    say("=" * 60)
    say("  Gold Bot - update")
    say("=" * 60)

    total = 4 if args.skip_tests else 5

    if not git_available():
        raise SystemExit(
            "git is not installed, so this script cannot fetch updates.\n"
            "Install it from https://git-scm.com/download/win and try again."
        )
    ensure_repo()

    branch = current_branch()
    step(1, total, f"Checking your copy (branch: {branch})")
    say(f"      currently at: {short_head()}")

    dirty = working_tree_dirty()
    if dirty:
        if args.stash:
            say(f"      {len(dirty)} local change(s) found - setting them aside")
            run(["git", "stash", "push", "-u", "-m", "update.py auto-stash"], check=True)
            say("      restore them later with:  git stash pop")
        else:
            say("\n  Stopped: you have uncommitted changes here.\n")
            for line in dirty[:20]:
                say(f"      {line}")
            if len(dirty) > 20:
                say(f"      … and {len(dirty) - 20} more")
            say("\n  Updating now could throw this work away, so nothing was changed.")
            say("  Pick one:")
            say("      python update.py --stash     set the changes aside, then update")
            say("      git checkout -- .            discard them permanently, then re-run")
            say("      git commit -am \"my changes\"   keep them as a commit, then re-run")
            return 1

    step(2, total, f"Fetching the latest code for {branch}")
    fetch_with_retries(branch)

    code, out = run(["git", "rev-list", "--count", f"HEAD..origin/{branch}"])
    behind = int(out) if code == 0 and out.isdigit() else 0
    if behind == 0:
        say("      already up to date - nothing new to download")
    else:
        say(f"      {behind} new commit(s) available")

    req_before = file_hash("requirements.txt")

    step(3, total, "Applying the update")
    if behind == 0:
        say("      nothing to apply")
    else:
        # --ff-only: refuse to create a merge or rewrite anything. If the
        # branches have diverged we stop and say so rather than guessing.
        code, out = run(["git", "merge", "--ff-only", f"origin/{branch}"])
        if code != 0:
            say("\n  Stopped: your branch has diverged from the server, so it")
            say("  cannot be updated automatically without risking your work.")
            say("  Nothing was changed. Details from git:\n")
            say(out)
            say("\n  If you have no local work worth keeping, this resets to the")
            say("  server's version (THIS DISCARDS LOCAL COMMITS):")
            say(f"      git reset --hard origin/{branch}")
            return 1
        say(f"      updated to: {short_head()}")

    req_after = file_hash("requirements.txt")
    if behind and req_before != req_after:
        say("      requirements.txt changed - installing new dependencies")
        code, out = run([sys.executable, "-m", "pip", "install", "-r",
                         "requirements.txt", "--quiet"])
        if code != 0:
            say("\n  Dependency install failed:\n")
            say(out)
            return 1
        say("      dependencies updated")
    elif behind:
        say("      dependencies unchanged")

    if not args.skip_tests:
        step(4, total, "Running tests to check the update is healthy")
        code, out = run([sys.executable, "-m", "pytest", "tests", "-q"])
        tail = "\n".join(out.splitlines()[-12:])
        say(tail)
        if code != 0:
            say("\n  Tests FAILED after updating.")
            say("  The code was updated but something is wrong - do not trade")
            say("  with it until this is resolved. To go back to the previous")
            say("  version:")
            say("      git reset --hard HEAD@{1}")
            return 1

    step(total, total, "Done")
    say(f"      branch:  {branch}")
    say(f"      version: {short_head()}")
    if dirty and args.stash:
        say("\n      your set-aside changes are still saved - run: git stash pop")
    say("\n  You can close this window and start the bot normally.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ncancelled - nothing was changed")
        raise SystemExit(130)

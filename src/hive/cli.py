"""HIVE CLI — the P0 owner surface (dashboard and Telegram come later).

  hive [--adapter DIR] <command>

  hive ask "<request>"           owner-initiated job (e.g. a market scan)
  hive demo                      run the pilot workflow end-to-end (offline stub model)
  hive status                    list jobs and their states
  hive approvals                 show pending approval cards
  hive approve <card_id> [note]  approve a card
  hive reject  <card_id> [note]  reject a card
  hive digest [--send]           print the daily digest (--send: deliver via sinks)
  hive ratify <step_key>         ratify a proposed autonomy upgrade
  hive trace <job_id>            dump a job's trace
  hive serve [--host --port]     run the HTTP API + UI + inbox watcher
  hive watch [--interval N]      watch <data_dir>/inbox for dropped files
  hive eval [--workflow N] [--backend stub|ollama|anthropic]
                                 run the adapter's golden eval cases
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hive.config import HiveConfig
from hive.events.bus import Event
from hive.governance.approvals import ApprovalStatus
from hive.governance.digest import build_digest
from hive.runtime import Runtime


def _load_dotenv() -> None:
    """Load the repo-root `.env` into os.environ before any `from_env()` runs, so
    a pasted `.env` configures HIVE (voice backend, keys, …). Shell vars win
    (override=False). No-op if python-dotenv or the file is absent — nothing here
    is required, and unit tests (which never call main()) are untouched."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def _runtime(args: argparse.Namespace) -> Runtime:
    config = HiveConfig.from_env()
    if getattr(args, "adapter", None):
        config = config.model_copy(update={"adapter_dir": Path(args.adapter)})
    return Runtime(config)


def _print_job_result(rt: Runtime, job) -> None:
    print(f"\njob {job.id} -> {job.state.value}")
    print(f"spend: {job.spend_tokens} tokens / ${job.spend_usd:.4f} / {job.owner_touches} owner touches")
    for card in rt.store.list_cards(status=ApprovalStatus.PENDING.value):
        if card.job_id == job.id:
            print("\n" + card.render())
            print(f"\nnext: hive approve {card.id}")


def cmd_ask(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    event = Event(
        type="owner.request",
        source="owner",
        metadata={"subject": args.request, "source": "owner:cli"},
    )
    job = rt.trigger(event)
    if job is None:
        print(f"adapter '{rt.adapter.name}' has no workflow with trigger 'owner.request'")
        return 1
    _print_job_result(rt, job)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    sop = next(iter(rt.adapter.workflows.values()), None)
    if sop is None:
        print("adapter has no workflows — add one under adapters/<name>/workflows/")
        return 1
    print(f"adapter:  {rt.adapter.name}")
    print(f"workflow: {sop.name} v{sop.version} (trigger: {sop.trigger})")
    event = Event(
        type=sop.trigger,
        source="manual",
        metadata={
            "subject": "Acme Interiors — 2,400 sq yd office fit-out enquiry",
            "raw_context": (
                "Hi, we saw your work on the Meridian project. We have a 2,400 sq yd "
                "office fit-out in Gurgaon, budget flexible, timeline 3 months. "
                "Can you send a proposal? — Priya, Acme Interiors"
            ),
            "source": "email:demo",
            "reply_to": "priya@acme.example",  # approve => proposal emailed here
        },
    )
    job = rt.trigger(event)
    if job is None:
        print("no workflow matched the event")
        return 1
    _print_job_result(rt, job)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    jobs = rt.store.list_jobs()
    if not jobs:
        print("no jobs yet — try: hive demo")
        return 0
    for job in sorted(jobs, key=lambda j: j.created_at):
        print(f"{job.id}  [{job.state.value:>18}]  {job.workflow}  "
              f"${job.spend_usd:.4f}  touches={job.owner_touches}")
    return 0


def cmd_approvals(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    cards = rt.store.list_cards(status=ApprovalStatus.PENDING.value)
    if not cards:
        print("no pending approvals")
        return 0
    for card in cards:
        print(card.render())
        print()
    return 0


def _decide(args: argparse.Namespace, status: ApprovalStatus) -> int:
    rt = _runtime(args)
    job = rt.coordinator.resolve_approval(args.card_id, status, note=args.note or "")
    print(f"{args.card_id} {status.value} -> job {job.id} is now {job.state.value}")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    print(build_digest(rt.store, rt.adapter.policies))
    if args.send:
        from hive.governance.delivery import DigestScheduler

        print("\ndelivering:")
        for result in DigestScheduler(rt).deliver():
            mark = "ok" if result["ok"] else "FAILED"
            print(f"  [{mark}] {result['sink']}: {result['detail']}")
    return 0


def cmd_ratify(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    if args.step_key not in rt.coordinator.gate.dial.steps:
        print(f"no autonomy record for step '{args.step_key}'")
        return 1
    level = rt.coordinator.ratify_autonomy(args.step_key)
    print(f"{args.step_key} -> {level.name}")
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    rt = _runtime(args)
    entries = rt.trace.read(args.job_id)
    if not entries:
        print(f"no trace for {args.job_id}")
        return 1
    for entry in entries:
        print(json.dumps(entry, ensure_ascii=False))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from hive.evals.harness import load_cases, run_suite

    config = HiveConfig.from_env()
    if args.adapter:
        config = config.model_copy(update={"adapter_dir": Path(args.adapter)})
    backend = args.backend or config.model_backend
    cases = load_cases(config.adapter_dir, workflow=args.workflow)
    if not cases:
        print(f"no eval cases under {config.adapter_dir / 'evals'}")
        return 1

    print(f"EVAL SUITE — adapter: {config.adapter_dir.name} · backend: {backend} · {len(cases)} case(s)")
    suite = run_suite(
        cases, adapter_dir=config.adapter_dir, backend=backend,
        history_dir=config.data_dir / "evals", ollama_model=config.ollama_model,
    )
    for case in suite.cases:
        mark = "PASS" if case.passed else "FAIL"
        print(f"  [{mark}] {case.workflow}/{case.name}  {case.summary}  "
              f"${case.spend_usd:.4f} / {case.spend_tokens} tok")
        for outcome in case.outcomes:
            if not outcome.ok:
                print(f"         x {outcome.check}" + (f"  ({outcome.detail})" if outcome.detail else ""))
    print(f"score: {suite.passed}/{len(suite.cases)}  ·  history: {config.data_dir / 'evals' / 'history.jsonl'}")
    return 0 if suite.passed == len(suite.cases) else 1


def cmd_watch(args: argparse.Namespace) -> int:
    from hive.events.sources import FileInboxSource

    rt = _runtime(args)
    source = FileInboxSource(rt)
    print(f"watching {source.inbox}  (adapter: {rt.adapter.name}, "
          f"default event type for .txt/.md: {source.default_type})")
    print("drop .json / .txt / .md files there; Ctrl+C to stop")
    try:
        source.watch(
            interval=args.interval,
            on_job=lambda job: print(f"  -> job {job.id} [{job.state.value}] from inbox"),
        )
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("the API server needs the 'api' extra: pip install -e \".[api]\"")
        return 1
    import threading

    from hive.api import create_app
    from hive.events.sources import FileInboxSource

    from hive.governance.delivery import DigestScheduler

    rt = _runtime(args)  # one long-lived runtime backs every request
    base = f"http://{args.host}:{args.port}"
    has_ui = (rt.config.web_dir / "index.html").exists()
    source = FileInboxSource(rt)
    threading.Thread(  # inbox watcher rides along with the server
        target=source.watch, name="hive-inbox-watcher", daemon=True
    ).start()
    scheduler = DigestScheduler(rt)
    threading.Thread(  # daily digest delivery rides along too
        target=scheduler.watch, name="hive-digest-scheduler", daemon=True
    ).start()
    print(f"HIVE on {base}  (adapter: {rt.adapter.name})")
    print(f"  UI:   {base}/" + ("" if has_ui else "   (no web/index.html yet — drop your export there)"))
    print(f"  API docs: {base}/docs")
    print(f"  inbox:    {source.inbox}  (drop .json/.txt/.md files to open jobs)")
    print(f"  webhook:  POST {base}/events  "
          + ("(enabled)" if rt.config.webhook_token else "(disabled — set HIVE_WEBHOOK_TOKEN)"))
    sinks = ", ".join(s.name for s in scheduler.sinks)
    print(f"  digest:   daily at {rt.config.digest_time} -> {sinks}"
          + ("" if rt.config.telegram_bot_token else "   (telegram dormant — set HIVE_TELEGRAM_BOT_TOKEN + HIVE_TELEGRAM_CHAT_ID)"))
    uvicorn.run(create_app(rt), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default to cp1252; artifacts are UTF-8 text.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _load_dotenv()  # let a repo-root .env configure HIVE (voice backend, keys, …)
    parser = argparse.ArgumentParser(prog="hive", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapter", "-a", default=None,
                        help="adapter directory to mount (overrides HIVE_ADAPTER_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask")
    p_ask.add_argument("request")
    p_ask.set_defaults(func=cmd_ask)

    sub.add_parser("demo").set_defaults(func=cmd_demo)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("approvals").set_defaults(func=cmd_approvals)

    p_digest = sub.add_parser("digest")
    p_digest.add_argument("--send", action="store_true")
    p_digest.set_defaults(func=cmd_digest)

    p_ratify = sub.add_parser("ratify")
    p_ratify.add_argument("step_key")
    p_ratify.set_defaults(func=cmd_ratify)

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("card_id")
    p_approve.add_argument("note", nargs="?", default="")
    p_approve.set_defaults(func=lambda a: _decide(a, ApprovalStatus.APPROVED))

    p_reject = sub.add_parser("reject")
    p_reject.add_argument("card_id")
    p_reject.add_argument("note", nargs="?", default="")
    p_reject.set_defaults(func=lambda a: _decide(a, ApprovalStatus.REJECTED))

    p_trace = sub.add_parser("trace")
    p_trace.add_argument("job_id")
    p_trace.set_defaults(func=cmd_trace)

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_eval = sub.add_parser("eval")
    p_eval.add_argument("--workflow", default=None)
    p_eval.add_argument("--backend", choices=["stub", "ollama", "anthropic"], default=None)
    p_eval.set_defaults(func=cmd_eval)

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--interval", type=float, default=None)
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

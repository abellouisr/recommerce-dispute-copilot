"""Dispute Copilot – start the local web app.

    python run.py            start the app at http://localhost:8501
    python run.py --eval     run the evaluation in the terminal
    python run.py --reset    regenerate the synthetic data
"""
import sys

# Windows consoles often use a code page that cannot print some characters; never crash on output.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.version_info < (3, 10):
    sys.exit("Python 3.10 or newer is needed. Download it from https://www.python.org/downloads/")

from copilot import store, web

if __name__ == "__main__":
    if "--reset" in sys.argv:
        store.reset()
        print("Synthetic data regenerated.")
    elif "--eval" in sys.argv:
        e = store.evaluate(store.connect())
        print(f"{e['n']} cases - {e['mode']} mode - fully correct {e['fully_correct']}% - cost ${e['cost_usd']}")
        if e.get("llm_failed"):
            print(f"  WARNING: Claude failed on {e['llm_failed']} cases - {e['llm_error']}")
        for k, v in e["accuracy"].items():
            print(f"  {k:<10} {v}%")
        for r in e["rows"]:
            if not r["all_ok"]:
                diff = {k: (r["expected"][k], r["got"][k]) for k, ok in r["ok"].items() if not ok}
                print(f"  miss {r['id']}: {diff}")
    else:
        web.serve(open_browser="--no-browser" not in sys.argv)

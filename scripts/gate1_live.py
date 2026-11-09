"""Gate 1 live check (OPT-IN, creates ONE real notebook). Run only after user consent.

Reuses tmp/gate1/state.json so re-running never creates a second notebook or re-uploads.
Prints only ids/status/timings; never credentials.
"""

import json
import sys
import time
from pathlib import Path

from bili2nb.errors import Bili2nbError
from bili2nb.notebook.adapter import NotebookAdapter, notebook_url

D = Path(__file__).resolve().parent.parent / "tmp" / "gate1"
STATE = D / "state.json"
FILES = {"audio": D / "gate1_audio.m4a", "pdf": D / "gate1_page.pdf"}


def main() -> int:
    st = json.loads(STATE.read_text()) if STATE.exists() else {"sources": {}}
    a = NotebookAdapter()
    t0 = time.time()
    try:
        print("auth ok, notebooks:", a.check_auth(), flush=True)
        if "notebook_id" not in st:
            st["notebook_id"] = a.create_notebook("[bili2nb Gate1 test] green giraffe / purple owl")
            STATE.write_text(json.dumps(st))
            print("created notebook", st["notebook_id"], f"{time.time()-t0:.1f}s", flush=True)
        nb = st["notebook_id"]
        for kind, path in FILES.items():
            if kind in st["sources"]:
                continue
            t = time.time()
            st["sources"][kind] = a.upload_file(nb, path, title=f"gate1-{kind}")
            STATE.write_text(json.dumps(st))
            print(f"uploaded {kind} -> {st['sources'][kind]} ({time.time()-t:.1f}s)", flush=True)
        for kind, sid in st["sources"].items():
            t = time.time()
            info = a.wait_ready(nb, sid, timeout=600)
            print(f"{kind}: {info.status} after {time.time()-t:.1f}s", flush=True)
        print("sources:", [(s.id, s.status) for s in a.list_sources(nb)])
        print("URL:", notebook_url(nb))
        print(f"total {time.time()-t0:.1f}s")
        return 0
    except Bili2nbError as e:
        print(f"FAILED [{e.code}] {e.message} detail={e.detail}", file=sys.stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())

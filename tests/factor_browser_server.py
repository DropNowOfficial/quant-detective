"""Synthetic local-server fixture for the supported Playwright entrypoint."""
from pathlib import Path
import signal
import sys
from threading import Event, Thread

from factors.importer import InstrumentUniverse
from market_data.server import make_server
from test_factor_bindings import reports


def forbidden_fetch(*args, **kwargs):
    raise AssertionError('Browser factor acceptance must never fetch a provider')


if __name__ == '__main__':
    app = make_server(0, forbidden_fetch, factor_store_path=Path(sys.argv[1]),
                      enable_factor_import=True, factor_reports=reports(),
                      factor_universe=InstrumentUniverse('browser-synthetic-v1', {'SYNTH': 'us_equity'}))
    done = Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    worker = Thread(target=app.serve_forever, daemon=True)
    worker.start()
    print(app.server_port, flush=True)
    try:
        done.wait()
    finally:
        app.shutdown()
        app.server_close()
        worker.join()

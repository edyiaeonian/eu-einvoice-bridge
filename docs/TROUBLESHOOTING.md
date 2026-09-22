# Troubleshooting

## `einvoice` fails with `ModuleNotFoundError` on macOS

If the command fails even though `pip list` shows the package installed, the venv is
probably inside an iCloud-synced folder — on macOS, Desktop and Documents are synced
by default.

Three things line up to produce a silent failure:

1. iCloud marks the `.venv` directory with the BSD `UF_HIDDEN` flag, and the flag
   spreads to files inside it
2. Python 3.14's `site.py` skips hidden `.pth` files
3. An editable install *is* a `.pth` file, so the package silently stops resolving

Nothing reports an error at any step. `chflags nohidden .venv/lib/*/site-packages/*.pth`
clears it, but iCloud re-applies the flag within minutes, so the fix is to put the
venv somewhere that is not synced:

```bash
python3 -m venv ~/.virtualenvs/eu-einvoice-bridge
~/.virtualenvs/eu-einvoice-bridge/bin/pip install -e ".[dev]"
```

Syncing a virtualenv is a bad idea regardless — thousands of files, none of them
worth keeping.

The test suite is unaffected either way: `pythonpath = ["src"]` in `pyproject.toml`
puts the source tree on `sys.path` directly, so tests never depend on the editable
install resolving.

## `pytest -m integration` fails

The integration test talks to Poland's KSeF TEST environment, which is down for
maintenance every day from 16:00 to 18:00 Warsaw time. A failure in that window says
nothing about this code. The same test runs daily on GitHub Actions; its badge in the
README shows whether the sandbox itself was reachable.

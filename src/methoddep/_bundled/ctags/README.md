# Bundled Universal Ctags

Drop platform binaries here so offline installs get a working `ctags` without
relying on `choco` / `apt`. Only Windows x64 and Linux x64 are supported.

## Expected layout

```
src/methoddep/_bundled/ctags/
├── __init__.py          # resolver — don't touch
├── README.md            # this file
├── windows-x64/
│   └── ctags.exe        # Universal Ctags for Win64, >= p6.x recommended
└── linux-x64/
    └── ctags            # Universal Ctags Linux x86_64 static or AppImage-extracted binary
```

## How to populate

```bash
# one-shot downloader (Python 3.11+, needs internet only on the build machine)
python scripts/fetch_bundled_ctags.py
```

The script pins a specific release, verifies SHA-256, and places the binary
exactly where the resolver expects it. See the script for which version /
download URL is pinned and how to bump.

## License note

Universal Ctags is GPL-2.0-or-later. Redistributing its binary alongside
methoddep (MIT) is generally treated as "mere aggregation" — the binary is
invoked as a separate process, not linked. If you publish methoddep wheels
to a public index, include a pointer to the Universal Ctags source in your
release notes. For internal / air-gapped distribution this is usually a
non-issue but confirm with your legal team if relevant.

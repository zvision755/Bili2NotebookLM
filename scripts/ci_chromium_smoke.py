#!/usr/bin/env python3
"""CI check: the default login browser (Playwright's bundled Chromium) can be installed through
bili2nb's own code path and started. Opens only a local page: no Google, no internet browsing.

Honest scope: CI runners already have Chrome/Edge, so this proves "the bundled Chromium installs and
launches on this OS", NOT "a machine without any browser can log in" (that needs a real clean machine).
"""

from __future__ import annotations

import sys

from bili2nb.notebook import auth


def main() -> int:
    if not auth.chromium_installed():
        auth.install_chromium()  # the same one-time download a first-time user triggers
    if not auth.chromium_installed():
        print("FAIL: Chromium still missing after install_chromium()", file=sys.stderr)
        return 1
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # a real window, like the interactive login
        try:
            page = browser.new_page()
            page.set_content("<title>ok</title><p>bili2nb</p>")
            if page.title() != "ok":
                print("FAIL: page did not render", file=sys.stderr)
                return 1
            print(f"OK: bundled Chromium {browser.version} launched on {sys.platform}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

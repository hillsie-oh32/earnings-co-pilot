"""
Keep the Earnings Copilot app awake.

Streamlit's free hosting puts an app to sleep after a period with no
visitors; the next visitor then sees a "This app has gone to sleep" page and
waits about 30 seconds. This script, run every few hours by GitHub Actions
(.github/workflows/keep-awake.yml), visits the app in a real headless browser
and, if it's asleep, clicks the button to wake it up.

Run locally with:  python .github/scripts/keep_awake.py   (needs: pip install playwright)
"""

import sys
import time

from playwright.sync_api import sync_playwright

APP = "https://earnings-co-pilot.streamlit.app/"
WAKE_BUTTON = "Yes, get this app back up"
LOADED = ".ec-title"  # the home page headline, inside the app's frame


def app_loaded(page):
    """True once the app's home page has rendered (in any frame)."""
    return any(frame.query_selector(LOADED) for frame in page.frames)


def main(channel=None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=channel)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(APP, wait_until="domcontentloaded", timeout=60_000)

        woke, deadline = False, time.time() + 180
        while time.time() < deadline:
            if app_loaded(page):
                print("App is awake" + (" (woken up by this visit)." if woke else "."))
                time.sleep(20)  # stay a moment, like a real visitor
                browser.close()
                return 0
            button = page.get_by_role("button", name=WAKE_BUTTON)
            if not woke and button.count():
                print("App was asleep: clicking the wake-up button.")
                button.first.click()
                woke = True
            time.sleep(5)

        print("The app didn't load within 3 minutes.")
        page.screenshot(path="keep-awake-failure.png")
        browser.close()
        return 1


if __name__ == "__main__":
    # Locally you can use an installed browser instead of Playwright's own:
    #   python keep_awake.py msedge
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))

"""Full-page screenshots of the public Metabase dashboards (for docs/img).

Runs inside the official Playwright image on the VM:
    docker run --rm --network host -v "$PWD/ops/metabase:/work" -v /tmp/shots:/out \
      mcr.microsoft.com/playwright/python:v1.63.0-noble \
      python /work/screenshots.py <name>=<public dashboard url> ...
"""

import sys

from playwright.sync_api import sync_playwright

WIDTH = 1440
SCALE = 2


def main(targets: list[str]) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": 900},
            device_scale_factor=SCALE,
            locale="pt-BR",
            color_scheme="light",
        )
        for target in targets:
            name, url = target.split("=", 1)
            page.goto(url, wait_until="networkidle", timeout=120_000)
            # Wait until every card finished loading (no spinners left).
            page.wait_for_function(
                "() => !document.querySelector('[data-testid=\"loading-indicator\"]')",
                timeout=120_000,
            )
            # Metabase scrolls inside an inner container, so full_page alone
            # stops at the viewport: grow the viewport to the content height.
            height = page.evaluate(
                "() => Math.max(...[...document.querySelectorAll('*')]"
                ".map(e => e.scrollHeight))"
            )
            page.set_viewport_size({"width": WIDTH, "height": min(height + 40, 6000)})
            page.wait_for_timeout(4000)  # re-layout and chart animations
            page.screenshot(path=f"/out/{name}.png", full_page=True)
            print(f"{name}.png")
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])

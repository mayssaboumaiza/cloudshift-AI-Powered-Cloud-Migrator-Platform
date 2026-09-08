"""
Capture each slide of architecture_slides.html as a PNG (1280x720).
Triggers the JS animation sequence per slide then waits for it to settle.
"""
import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright

HTML_FILE = Path(__file__).parent / "architecture_slides.html"
OUT_DIR   = Path(__file__).parent / "slides_png"
TOTAL     = 5
TITLES    = [
    "slide_01_vue_ensemble",
    "slide_02_presentation_orchestration",
    "slide_03_agents_ia",
    "slide_04_donnees_securite",
    "slide_05_services_cloud",
]

async def main():
    OUT_DIR.mkdir(exist_ok=True)
    url = HTML_FILE.as_uri()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        print(f"Loading: {url}")
        await page.goto(url, wait_until="networkidle")

        # Inject a style that disables all CSS transitions so nothing stays hidden
        await page.add_style_tag(content="""
            .anim, .anim-x, .anim-s {
                opacity: 1 !important;
                transform: none !important;
                transition: none !important;
            }
        """)
        await page.wait_for_timeout(300)

        for idx in range(TOTAL):
            if idx > 0:
                await page.evaluate(f"goTo({idx})")
                await page.wait_for_timeout(150)

            # Force every animated element visible (bypasses CSS transition opacity:0)
            await page.evaluate(f"""
                () => {{
                    const slide = document.getElementById('s{idx + 1}');
                    if (slide) {{
                        slide.querySelectorAll('.anim,.anim-x,.anim-s').forEach(el => {{
                            el.style.opacity = '1';
                            el.style.transform = 'none';
                            el.classList.add('visible');
                        }});
                    }}
                    // also call global showAll for any leftovers
                    if (typeof showAll === 'function') showAll();
                }}
            """)
            await page.wait_for_timeout(300)

            out_path = OUT_DIR / f"{TITLES[idx]}.png"
            deck = page.locator("#deck")
            await deck.screenshot(path=str(out_path))
            print(f"  Saved: {out_path.name}")

        await browser.close()
    print("\nDone. PNGs in:", OUT_DIR)

asyncio.run(main())

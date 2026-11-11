"""Capturas de pantalla (412x915) de la app con Playwright + Chrome del sistema.
Uso: python3 tools_screens.py [URL_BASE]"""
import re, sys
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
PASS = re.search(r"Contraseña: (\S+)", open("DEMO_LOGIN.txt").read()).group(1)
STATIC_NAV = ".tabs{position:static}"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
    ctx = b.new_context(viewport={"width": 412, "height": 915}, device_scale_factor=2, is_mobile=True,
                        has_touch=True, locale="es-CA")
    pg = ctx.new_page()
    pg.goto(BASE + "/entrar"); pg.screenshot(path="screens/01-entrar.png")
    pg.fill("input[name=email]", "demo@receiptomatic.test"); pg.fill("input[name=password]", PASS)
    pg.click("button[type=submit]"); pg.wait_for_url(BASE + "/")
    pg.screenshot(path="screens/02-inicio.png")
    for path, name in (("/recibo/1", "03-recibo-walmart"), ("/recibo/2", "04-recibo-superc-revisar"),
                       ("/recibo/3", "06-recibo-corregido"), ("/tablero", "05-tablero")):
        pg.goto(BASE + path); pg.add_style_tag(content=STATIC_NAV)
        pg.screenshot(path=f"screens/{name}.png", full_page=True)
    pg.goto(BASE + "/"); pg.wait_for_timeout(1500)
    print("service workers registrados:", pg.evaluate("navigator.serviceWorker.getRegistrations().then(r=>r.length)"))
    b.close()

"""Receipt-o-Matic (prototipo) — FastAPI + SQLite + Tesseract."""
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

import bcrypt
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ocr import ocr_receipt
from parse import parse, score, reconcile, categorize, categories, main_category

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get("DATA_DIR") or os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")
DB = os.path.join(DATA, "app.db")
os.makedirs(UPLOADS, exist_ok=True)
HTTPS_ONLY = os.environ.get("HTTPS_ONLY", "0") == "1"

SECRET = os.environ.get("SECRET_KEY")
if not SECRET:
    _secret_file = os.path.join(DATA, "secret.key")
    if not os.path.exists(_secret_file):
        with open(_secret_file, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(_secret_file, 0o600)
    SECRET = open(_secret_file).read().strip()

app = FastAPI(title="Receipt-o-Matic")
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="rom_s",
                   max_age=60 * 60 * 24 * 30, same_site="lax", https_only=HTTPS_ONLY)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
MAX_UPLOAD = 15 * 1024 * 1024
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def fmt(v):
    if v is None:
        return "—"
    s = f"{abs(v):,.2f}".replace(",", " ").replace(".", ",")
    return ("-" if v < 0 else "") + s + " $"


def mes(ym):
    try:
        y, m = ym.split("-")
        return f"{MESES[int(m)].capitalize()} {y}"
    except Exception:
        return ym


templates.env.filters["money"] = fmt
templates.env.filters["mes"] = mes


# ---------------- base de datos ----------------
@contextmanager
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, pw_hash TEXT NOT NULL,
        created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS receipts(
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, photo TEXT NOT NULL, merchant TEXT, date TEXT,
        total REAL, subtotal REAL, tps REAL, tvq REAL, category TEXT, ocr_text TEXT,
        ocr_variant TEXT, edited INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS lines(
        id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
        idx INTEGER, name TEXT, price REAL, category TEXT, familia TEXT, manual INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS ix_r_user ON receipts(user_id);
    """)


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    with db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def need_user(request):
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=303, headers={"Location": "/entrar"})
    return u


def get_receipt(user, rid):
    with db() as c:
        r = c.execute("SELECT * FROM receipts WHERE id=? AND user_id=?", (rid, user["id"])).fetchone()
        if not r:
            raise HTTPException(404, "Recibo no encontrado")
        ls = c.execute("SELECT * FROM lines WHERE receipt_id=? ORDER BY idx", (rid,)).fetchall()
    return r, ls


def receipt_status(r, ls):
    items = [{"price": l["price"]} for l in ls]
    s, diff, st = reconcile(items, r["total"], r["tps"], r["tvq"])
    return {"suma": s, "diff": diff, "estado": st}


def render(request, name, **ctx):
    ctx.setdefault("user", current_user(request))
    return templates.TemplateResponse(request, name, ctx)


# ---------------- PWA ----------------
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(HERE, "static", "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(os.path.join(HERE, "static", "sw.js"), media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/salud")
def salud():
    return {"ok": True}


# ---------------- cuentas ----------------
@app.get("/entrar", response_class=HTMLResponse)
def entrar_form(request: Request):
    return render(request, "login.html", modo="entrar", error=None)


@app.post("/entrar")
def entrar(request: Request, email: str = Form(...), password: str = Form(...)):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not u or not bcrypt.checkpw(password.encode(), u["pw_hash"].encode()):
        return render(request, "login.html", modo="entrar", error="Correo o contraseña incorrectos.")
    request.session.clear()
    request.session["uid"] = u["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/registro", response_class=HTMLResponse)
def registro_form(request: Request):
    return render(request, "login.html", modo="registro", error=None)


@app.post("/registro")
def registro(request: Request, email: str = Form(...), name: str = Form(""), password: str = Form(...)):
    email = email.strip().lower()
    if "@" not in email or len(password) < 6:
        return render(request, "login.html", modo="registro",
                      error="Usa un correo válido y una contraseña de al menos 6 caracteres.")
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(email,name,pw_hash,created_at) VALUES(?,?,?,?)",
                            (email, name.strip(), h, datetime.now().isoformat(timespec="seconds")))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return render(request, "login.html", modo="registro", error="Ese correo ya tiene cuenta.")
    request.session.clear()
    request.session["uid"] = uid
    return RedirectResponse("/", status_code=303)


@app.get("/salir")
def salir(request: Request):
    request.session.clear()
    return RedirectResponse("/entrar", status_code=303)


# ---------------- inicio / subir ----------------
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/entrar", status_code=303)
    with db() as c:
        rs = c.execute("SELECT * FROM receipts WHERE user_id=? ORDER BY COALESCE(date, substr(created_at,1,10)) DESC, id DESC LIMIT 5",
                       (u["id"],)).fetchall()
        mes_actual = datetime.now().strftime("%Y-%m")
        tot_mes = c.execute("SELECT SUM(total) FROM receipts WHERE user_id=? AND substr(COALESCE(date, created_at),1,7)=?",
                            (u["id"], mes_actual)).fetchone()[0]
    recibos = []
    for r in rs:
        _, ls = get_receipt(u, r["id"])
        recibos.append((r, receipt_status(r, ls)))
    return render(request, "home.html", recibos=recibos, tot_mes=tot_mes, mes_actual=mes_actual)


@app.post("/subir")
async def subir(request: Request, foto: UploadFile = File(...)):
    u = need_user(request)
    data = await foto.read()
    if not data:
        raise HTTPException(400, "Archivo vacío")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "La foto es muy grande (máx. 15 MB)")
    ext = ".jpg"
    if data[:8].startswith(b"\x89PNG"):
        ext = ".png"
    elif data[8:12] == b"WEBP":
        ext = ".webp"
    elif not data[:3] == b"\xff\xd8\xff":
        raise HTTPException(400, "Sube una imagen JPG, PNG o WEBP")
    fname = f"{u['id']}_{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOADS, fname)
    with open(path, "wb") as f:
        f.write(data)
    from starlette.concurrency import run_in_threadpool
    rid = await run_in_threadpool(process_receipt, u["id"], fname, path)
    return RedirectResponse(f"/recibo/{rid}?nuevo=1", status_code=303)


def process_receipt(uid, fname, path):
    try:
        texts, info = ocr_receipt(path)
        parsed = [(parse(t), v, t) for v, t, _ in texts]
        best, variant, text = max(parsed, key=lambda x: score(x[0]))
    except Exception as e:  # nunca perder la foto aunque falle el OCR
        best, variant, text = {"merchant": "", "date": "", "total": None, "subtotal": None,
                               "tps": None, "tvq": None, "items": []}, "error", f"ERROR OCR: {e}"
    with db() as c:
        cur = c.execute(
            "INSERT INTO receipts(user_id,created_at,photo,merchant,date,total,subtotal,tps,tvq,category,ocr_text,ocr_variant)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, datetime.now().isoformat(timespec="seconds"), fname, best["merchant"], best["date"] or None,
             best["total"], best["subtotal"], best["tps"], best["tvq"], main_category(best["items"]), text, variant))
        rid = cur.lastrowid
        for i, it in enumerate(best["items"]):
            c.execute("INSERT INTO lines(receipt_id,idx,name,price,category,familia) VALUES(?,?,?,?,?,?)",
                      (rid, i, it["name"], it["price"], it["category"], it["familia"]))
    return rid


# ---------------- recibo ----------------
@app.get("/recibo/{rid}", response_class=HTMLResponse)
def recibo(request: Request, rid: int, nuevo: int = 0):
    u = need_user(request)
    r, ls = get_receipt(u, rid)
    return render(request, "receipt.html", r=r, lines=ls, st=receipt_status(r, ls), nuevo=nuevo,
                  cats=categories())


@app.get("/recibo/{rid}/foto")
def recibo_foto(request: Request, rid: int):
    u = need_user(request)
    r, _ = get_receipt(u, rid)
    return FileResponse(os.path.join(UPLOADS, r["photo"]), headers={"Cache-Control": "private, max-age=3600"})


def _num(s):
    s = (s or "").strip().replace("$", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


@app.post("/recibo/{rid}/guardar")
async def guardar(request: Request, rid: int):
    u = need_user(request)
    r, old = get_receipt(u, rid)
    form = await request.form()
    oldmap = {l["id"]: l for l in old}
    with db() as c:
        c.execute("UPDATE receipts SET merchant=?, date=?, total=?, tps=?, tvq=?, edited=1 WHERE id=? AND user_id=?",
                  (form.get("merchant", "").strip(), form.get("date") or None, _num(form.get("total")),
                   _num(form.get("tps")), _num(form.get("tvq")), rid, u["id"]))
        c.execute("DELETE FROM lines WHERE receipt_id=?", (rid,))
        ids = form.getlist("line_id")
        names, prices, cats = form.getlist("line_name"), form.getlist("line_price"), form.getlist("line_cat")
        borrar = set(form.getlist("line_del"))
        idx = 0
        for lid, n, p, cat in zip(ids, names, prices, cats):
            n = n.strip()
            price = _num(p)
            if lid in borrar or (not n and price is None):
                continue
            prev = oldmap.get(int(lid)) if lid.isdigit() else None
            cat = cat.strip()
            manual = 0
            if not cat or (prev and prev["name"] != n and cat == prev["category"] and not prev["manual"]):
                cat, fam, _ = categorize(n)       # recategorizar automáticamente si cambió el nombre
            else:
                fam = categorize(n)[1] if categorize(n)[0] == cat else ""
                manual = 1 if (not prev or cat != prev["category"] or prev["manual"]) else 0
            c.execute("INSERT INTO lines(receipt_id,idx,name,price,category,familia,manual) VALUES(?,?,?,?,?,?,?)",
                      (rid, idx, n or "(sin nombre)", price, cat, fam, manual))
            idx += 1
        ls = c.execute("SELECT * FROM lines WHERE receipt_id=?", (rid,)).fetchall()
        c.execute("UPDATE receipts SET category=? WHERE id=?",
                  (main_category([{"category": l["category"], "price": l["price"]} for l in ls]), rid))
    return RedirectResponse(f"/recibo/{rid}?ok=1", status_code=303)


@app.post("/recibo/{rid}/borrar")
def borrar(request: Request, rid: int):
    u = need_user(request)
    r, _ = get_receipt(u, rid)
    with db() as c:
        c.execute("DELETE FROM receipts WHERE id=? AND user_id=?", (rid, u["id"]))
    try:
        os.remove(os.path.join(UPLOADS, r["photo"]))
    except OSError:
        pass
    return RedirectResponse("/tablero", status_code=303)


# ---------------- tablero ----------------
@app.get("/tablero", response_class=HTMLResponse)
def tablero(request: Request, mes_sel: str = ""):
    u = need_user(request)
    with db() as c:
        rs = c.execute("SELECT * FROM receipts WHERE user_id=? ORDER BY COALESCE(date, substr(created_at,1,10)) DESC, id DESC",
                       (u["id"],)).fetchall()
        meses = c.execute("""SELECT substr(COALESCE(date, created_at),1,7) AS ym, SUM(total) AS t, COUNT(*) AS n
                             FROM receipts WHERE user_id=? GROUP BY ym ORDER BY ym DESC""", (u["id"],)).fetchall()
        q = """SELECT l.category AS cat, SUM(l.price) AS t, COUNT(*) AS n FROM lines l JOIN receipts r ON r.id=l.receipt_id
               WHERE r.user_id=? {} GROUP BY l.category ORDER BY t DESC"""
        args = [u["id"]]
        filt = ""
        if mes_sel:
            filt = "AND substr(COALESCE(r.date, r.created_at),1,7)=?"
            args.append(mes_sel)
        cats = c.execute(q.format(filt), args).fetchall()
        tax = c.execute(f"SELECT SUM(COALESCE(tps,0)+COALESCE(tvq,0)) FROM receipts r WHERE user_id=? {filt}", args).fetchone()[0]
    recibos = []
    for r in rs:
        _, ls = get_receipt(u, r["id"])
        recibos.append((r, receipt_status(r, ls)))
    cats = [dict(cat=x["cat"], t=x["t"] or 0, n=x["n"]) for x in cats]
    if tax:
        cats.append(dict(cat="Impuestos (TPS + TVQ)", t=tax, n=0))
        cats.sort(key=lambda x: -x["t"])
    maxc = max([x["t"] for x in cats] + [1])
    maxm = max([m["t"] or 0 for m in meses] + [1])
    return render(request, "dashboard.html", recibos=recibos, meses=meses, cats=cats, maxc=maxc, maxm=maxm,
                  mes_sel=mes_sel)


# ---------------- borrar cuenta ----------------
@app.post("/cuenta/borrar")
def borrar_cuenta(request: Request, password: str = Form(...)):
    u = need_user(request)
    if not bcrypt.checkpw(password.encode(), u["pw_hash"].encode()):
        return RedirectResponse("/tablero?err=clave", status_code=303)
    with db() as c:
        fotos = [r["photo"] for r in c.execute("SELECT photo FROM receipts WHERE user_id=?", (u["id"],))]
        c.execute("DELETE FROM users WHERE id=?", (u["id"],))
    for f in fotos:
        try:
            os.remove(os.path.join(UPLOADS, f))
        except OSError:
            pass
    request.session.clear()
    return RedirectResponse("/entrar?borrada=1", status_code=303)

"""Parser heurístico de recibos de Quebec + categorización con DimCategoria."""
import json
import os
import re
import unicodedata
import difflib
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

MERCHANTS = [
    "Walmart", "Super C", "Dollarama", "Jean Coutu", "Maxi", "IGA", "Metro", "Costco",
    "Pharmaprix", "Canadian Tire", "Provigo", "Dollar Tree", "Familiprix", "Uniprix",
    "Brunet", "Giant Tiger", "Winners", "Adonis", "Tim Hortons", "McDonald's", "Couche-Tard",
    "Home Depot", "Rona", "Bureau en Gros", "Simons", "Sports Experts", "Decathlon",
    "Amazon", "Postes Canada", "Ultramar", "Esso", "Shell", "Petro-Canada", "Recreofun",
    "Super Marche", "Marche Tradition", "Loblaws", "Avril", "Sobeys", "Saan", "Hart",
]
MERCH_ALIASES = {"WAIMART": "Walmart", "WAL-MART": "Walmart", "WALMART": "Walmart",
                 "SUPER C": "Super C", "SUPERC": "Super C", "JEAN COUTU": "Jean Coutu",
                 "SUPERCSONDAGE": "Super C"}

MONTHS = {"JAN": 1, "JANV": 1, "FEV": 2, "FEVR": 2, "FEB": 2, "MARS": 3, "MAR": 3, "AVR": 4, "APR": 4,
          "MAI": 5, "MAY": 5, "JUIN": 6, "JUN": 6, "JUIL": 7, "JUL": 7, "AOUT": 8, "AUG": 8,
          "SEPT": 9, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

SKIP_WORDS = ["TOTAL", "T.P.S", "TPS", "T.V.Q", "TVQ", "TVH", "GST", "QST", "HST", "TAXE", "TAX",
              "MONNAIE", "CHANGE", "COMPTANT", "CASH", "VISA", "MASTERCARD", "DEBIT", "DÉBIT", "CREDIT",
              "INTERAC", "CAD$", "PMNT", "PAIEMENT", "PAYMENT", "COMPTE", "SOLDE", "ARTICLES", "ITEM",
              "APPROUV", "ACHAT", "MONTANT", "REMIS", "POINTS", "ECONOMIES", "VOUS AVEZ", "TEL", "AUTOR"]

PRICE_RE = re.compile(r"(-?\$?\s?\d{1,4}\s?[.,]\s?\d{2})\s*(-)?\s*\$?\s*[A-Za-z§$]{0,3}\s*$")
AMOUNT_RE = re.compile(r"\$?\s?(\d{1,5}[.,]\d{2})")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper()


def money(s):
    s = s.replace("$", "").replace(" ", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


# ---------------- categorías ----------------
_DIM = None


def dim():
    global _DIM
    if _DIM is None:
        rows = json.load(open(os.path.join(HERE, "dim_categoria.json"), encoding="utf-8"))
        for r in rows:
            r["_k"] = norm(r["PalabraClave"])
        rows.sort(key=lambda r: (r["Prioridad"] if r["Prioridad"] is not None else 99, -len(r["_k"]), r["orden"]))
        _DIM = rows
    return _DIM


def categories():
    return sorted({r["CategoriaEstandar"] for r in dim()} | {"Sin identificar"})


def categorize(name):
    """Coincidencia literal sin mayúsculas/acentos; prioridad menor gana; empate: palabra más larga."""
    n = norm(name)
    n = re.sub(r"(?<=[A-Z])0|0(?=[A-Z])", "O", n)  # M0ZZ -> MOZZ (OCR)
    for r in dim():
        k = r["_k"]
        if not k:
            continue
        # palabras cortas (<=3) deben coincidir como palabra completa para evitar falsos positivos
        if len(k) <= 3:
            if re.search(r"(?<![A-Z0-9])" + re.escape(k) + r"(?![A-Z0-9])", n):
                return r["CategoriaEstandar"], r["Familia"], r["PalabraClave"]
        elif k in n:
            return r["CategoriaEstandar"], r["Familia"], r["PalabraClave"]
    return "Sin identificar", "", None


# ---------------- campos de cabecera ----------------
def find_merchant(lines):
    head = [norm(l) for l in lines[:15]]
    joined = "\n".join(norm(l) for l in lines)
    for alias, m in MERCH_ALIASES.items():
        if alias in "\n".join(head):
            return m
    for m in MERCHANTS:
        if norm(m) in "\n".join(head):
            return m
    # aproximado (errores de OCR)
    for l in head:
        words = re.findall(r"[A-Z][A-Z\-' ]{3,}", l)
        for w in words:
            hit = difflib.get_close_matches(w.strip(), [norm(m) for m in MERCHANTS], n=1, cutoff=0.8)
            if hit:
                return next(m for m in MERCHANTS if norm(m) == hit[0])
    for alias, m in MERCH_ALIASES.items():
        if alias in joined:
            return m
    for l in lines[:6]:
        if PRICE_RE.search(l) or "TOTAL" in norm(l).replace("U", "O") or re.search(r"\d{3,}", l):
            continue
        t = re.sub(r"[^A-Za-zÀ-ÿ0-9 '&\-]", "", l).strip()
        letters = re.sub(r"[^A-Za-z]", "", t)
        if len(letters) >= 4 and len(letters) >= 0.8 * len(t.replace(" ", "")) and len(set(letters.upper())) >= 3:
            return t.title()[:40]
    return ""


def _valid(y, m, d):
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    if not (2015 <= y <= date.today().year + 1):
        return None
    return dt


def find_date(lines, merchant=""):
    today = date.today()
    cands = []
    text = "\n".join(norm(l) for l in lines)
    for m in re.finditer(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text):
        dt = _valid(int(m[1]), int(m[2]), int(m[3]))
        if dt: cands.append((3, dt))
    for m in re.finditer(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)", text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        dt = _valid(y, b, a) or _valid(y, a, b)  # Quebec: DD/MM/AAAA
        if dt: cands.append((3, dt))
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*(JANV?|FEVR?|FEB|MARS|MAR|AVR|APR|MAI|MAY|JUIN|JUN|JUIL|JUL|AOUT|AUG|SEPT?|OCT|NOV|DEC)[A-Z.]*\s*(20\d{2})", text):
        dt = _valid(int(m[3]), MONTHS[m[2]], int(m[1]))
        if dt: cands.append((3, dt))
    for m in re.finditer(r"(?<![\d/])(\d{2})/(\d{2})/(\d{2})(?![\d/])", text):
        a, b, c = int(m[1]), int(m[2]), int(m[3])
        opts = []
        if merchant == "Walmart":
            opts = [_valid(2000 + c, a, b)]          # MM/DD/AA
        opts += [_valid(2000 + a, b, c), _valid(2000 + c, b, a), _valid(2000 + c, a, b)]
        for dt in opts:
            if dt and dt <= today:
                cands.append((2, dt)); break
    cands = [c for c in cands if c[1] <= today]
    if not cands:
        return ""
    cands.sort(key=lambda c: -c[0])
    return cands[0][1].isoformat()


def _amount_at_end(line):
    m = PRICE_RE.search(line.strip())
    if not m:
        return None
    before = line.strip()[:m.start()].rstrip()
    if re.search(r"[@]\s*\$?$", before) or re.search(r"/\s*K", line[m.end():].upper()):
        return None  # "2 @ $8.53": precio unitario, no el importe de la línea
    v = money(m[1])
    if v is None:
        return None
    if m[2] == "-" or m[1].strip().startswith("-"):
        v = -abs(v)
    return v


def find_totals(lines):
    total = subtotal = None
    tps = tvq = 0.0
    tps_found = tvq_found = False
    for l in lines:
        n = norm(l)
        amt = _amount_at_end(l)
        if amt is None:
            continue
        compact = re.sub(r"[\s.\-—]", "", n)
        if re.search(r"S[O0]U[S5]T[O0U]T[A4]L|SUBT[O0]TAL|S[O0]U[S5]T[O0U]T", compact):
            subtotal = amt
        elif re.search(r"(^|[^A-Z])(TPS|GST|TPSTVH)", compact) or re.search(r"^TP[S5]", compact):
            if "%" in n or len(n) < 40:
                tps += amt; tps_found = True
        elif re.search(r"(TVQ|QST|TV0|TVA$|^TVA|^TVO)", compact):
            tvq += amt; tvq_found = True
        elif re.search(r"TOTAL", compact) and total is None:
            total = amt
    # algunos recibos: la palabra TPS/TVQ en una línea y el monto en otra; ignorado en el prototipo
    return {"total": total, "subtotal": subtotal, "tps": round(tps, 2) if tps_found else None,
            "tvq": round(tvq, 2) if tvq_found else None}


QTY_RE = re.compile(r"^\s*[\.\d]+\s*(KG|LB|G)?\s*[@8à]\s*\$?\s*\d", re.I)


def find_items(lines):
    items = []
    pending = None
    for l in lines:
        n = norm(l).strip()
        compact = re.sub(r"[\s.\-—]", "", n)
        if re.search(r"S[O0]U[S5]T[O0U]T[A4]L|SUBT[O0]TAL|^T[O0]TAL", compact):
            break
        amt = _amount_at_end(l)
        if amt is None:
            if len(re.sub(r"[^A-Z]", "", n)) >= 3:
                pending = l.strip()
            continue
        if any(w in n for w in SKIP_WORDS):
            continue
        name = PRICE_RE.sub("", l).strip()
        if QTY_RE.match(name) and pending:   # "2 @ $8.53   17.06" -> nombre de la línea anterior
            name = pending + " (" + name + ")"
        name = re.sub(r"\s\d{8,14}\b", "", name)      # códigos UPC (Walmart)
        name = re.sub(r"\s+(FP|F-P|F—P|P|FPS|\$[A-Z])$", "", name)
        name = re.sub(r"\s{2,}\S{1,2}$", "", name)            # basura de columnas ("—P", "FP")
        name = re.sub(r"\s{2,}", " ", name)
        name = re.sub(r"^[^A-Za-z0-9(]+", "", name).strip(" _-—|")
        pending = None
        if len(re.sub(r"[^A-Za-z]", "", name)) < 2:
            name = name or "(sin nombre)"
        items.append({"name": name[:60], "price": amt})
    return items


def parse(text):
    lines = [l for l in text.splitlines() if l.strip()]
    merchant = find_merchant(lines)
    res = {"merchant": merchant, "date": find_date(lines, merchant)}
    res.update(find_totals(lines))
    items = find_items(lines)
    for it in items:
        cat, fam, kw = categorize(it["name"])
        it.update(category=cat, familia=fam, keyword=kw, flagged=(cat == "Sin identificar"))
    res["items"] = items
    return res


def reconcile(items, total, tps, tvq, tol=0.02):
    s = round(sum(i["price"] or 0 for i in items) + (tps or 0) + (tvq or 0), 2)
    if total is None:
        return s, None, "Revisar"
    diff = round(total - s, 2)
    return s, diff, ("Cuadra" if abs(diff) <= tol else "Revisar")


def score(p):
    s, diff, st = reconcile(p["items"], p["total"], p["tps"], p["tvq"])
    known = p["merchant"] in MERCHANTS
    return known * 15 + (st == "Cuadra") * 100 + (p["total"] is not None) * 20 + bool(p["date"]) * 10 + bool(p["merchant"]) * 5 + len(p["items"])


def main_category(items):
    by = {}
    for i in items:
        by[i["category"]] = by.get(i["category"], 0) + (i["price"] or 0)
    if not by:
        return "Sin identificar"
    return max(by, key=by.get)

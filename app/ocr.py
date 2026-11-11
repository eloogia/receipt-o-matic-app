"""OCR de recibos: preprocesado (OpenCV/Pillow) + Tesseract (fra+eng) vía tesserocr."""
import os
import cv2
import numpy as np
from PIL import Image, ImageOps
import tesserocr

TESSDATA = os.environ.get("TESSDATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tessdata")
TESSDATA = TESSDATA.rstrip("/") + "/"


def _load(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def _crop_receipt(img):
    """Busca el papel (zona clara más grande) y lo recorta; corrige inclinación leve."""
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return img
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.12 * small.shape[0] * small.shape[1]:
        return img
    (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
    # normalizar ángulo a [-45, 45]
    if ang > 45:
        ang -= 90
    if abs(ang) > 0.5 and abs(ang) < 20:
        M = cv2.getRotationMatrix2D((cx / scale, cy / scale), ang, 1.0)
        img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
        small = cv2.resize(img, (int(w * scale), int(h * scale)))
        g2 = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (7, 7), 0)
        _, th = cv2.threshold(g2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return img
        c = max(cnts, key=cv2.contourArea)
    x, y, bw, bh = cv2.boundingRect(c)
    m = 10
    x0, y0 = max(0, int((x - m) / scale)), max(0, int((y - m) / scale))
    x1, y1 = min(w, int((x + bw + m) / scale)), min(h, int((y + bh + m) / scale))
    return img[y0:y1, x0:x1]


def _binarize(gray):
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    return th


def _conf_score(api, im):
    api.SetImage(Image.fromarray(im))
    api.Recognize()
    return sum(c for c in api.AllWordConfidences() if c > 30)


def _orientation(gray):
    """Rotación necesaria (0/90/180/270). OSD propone; se confirma comparando
    la confianza de Tesseract contra la imagen sin rotar."""
    small = gray
    h, w = gray.shape
    s = 1600.0 / max(h, w)
    if s < 1:
        small = cv2.resize(gray, (int(w * s), int(h * s)))
    cands = [0]
    try:
        with tesserocr.PyTessBaseAPI(path=TESSDATA, lang="osd", psm=tesserocr.PSM.OSD_ONLY) as api:
            api.SetImage(Image.fromarray(small))
            r = api.DetectOrientationScript()
            if r and r.get("orient_deg") not in (None, 0):
                cands.append(r["orient_deg"])
    except Exception:
        pass
    if w > h * 1.2:  # foto apaisada: el recibo seguro está de lado
        cands += [d for d in (90, 270) if d not in cands]
    if len(cands) == 1:
        return 0
    with tesserocr.PyTessBaseAPI(path=TESSDATA, lang="fra+eng", psm=tesserocr.PSM.SPARSE_TEXT) as api:
        scores = {d: _conf_score(api, _rot(small, d)) for d in cands}
    return max(scores, key=scores.get)


def _rot(img, deg):
    if deg == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def ocr_receipt(path):
    """Devuelve (texto, info_dict)."""
    img = _load(path)
    info = {}
    # 1) orientación sobre la imagen completa
    gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    deg = _orientation(gray_full)
    # OSD da el ángulo en sentido antihorario de la página -> rotar al revés
    fix = {0: 0, 90: 90, 180: 180, 270: 270}[deg]
    img = _rot(img, fix)
    info["rotacion"] = fix
    # 2) recortar papel + enderezar
    img = _crop_receipt(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 3) escalar: ancho del recibo ~1400 px
    h, w = gray.shape
    s = 1400.0 / w
    if s > 1.15 or s < 0.85:
        gray = cv2.resize(gray, (int(w * s), int(h * s)), interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    bw = _binarize(gray)
    bg = cv2.medianBlur(cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8)), 21)
    _, flat = cv2.threshold(cv2.divide(gray, bg, scale=255), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    texts = []
    for variant, im, psm in (("bin", bw, tesserocr.PSM.SINGLE_COLUMN),
                             ("gris", gray, tesserocr.PSM.SINGLE_COLUMN),
                             ("sombra", flat, tesserocr.PSM.SINGLE_COLUMN)):
        with tesserocr.PyTessBaseAPI(path=TESSDATA, lang="fra+eng", psm=psm) as api:
            api.SetVariable("preserve_interword_spaces", "1")
            api.SetImage(Image.fromarray(im))
            txt = api.GetUTF8Text()
            texts.append((variant, txt, api.MeanTextConf()))
    info["variantes"] = [(v, c) for v, _, c in texts]
    return texts, info

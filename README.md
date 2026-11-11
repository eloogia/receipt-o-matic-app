# Receipt-o-Matic: prototipo de app pública

Prototipo funcional. La persona toma la foto del recibo con el celular Android, la foto se sube a **su** perfil
y la app le devuelve el gasto leído, dividido en productos y categorizado con el diccionario `DimCategoria`.

Todo corre en un solo proceso Python, sin servicios pagos.

## Arquitectura

```
Android Chrome (PWA instalable)
  │  <input type=file accept=image/* capture=environment>  →  reduce la foto a ≤2600 px en el navegador
  ▼  HTTPS (túnel) → FastAPI (app/main.py)
  ├─ Cuentas: correo + contraseña (bcrypt) en SQLite; sesión con cookie firmada (Starlette SessionMiddleware)
  ├─ Fotos: data/uploads/<user>_<uuid>.jpg; solo se sirven por /recibo/{id}/foto tras comprobar el dueño
  ├─ OCR (app/ocr.py): Pillow (EXIF) → OpenCV: orientación (OSD de Tesseract + comprobación por confianza),
  │    recorte del papel, corrección de inclinación, escala a ~1400 px, 3 variantes
  │    (umbral adaptativo / gris / sin sombra) → Tesseract 5 (fra+eng) vía tesserocr
  ├─ Parser (app/parse.py): comercio (lista de cadenas de Quebec + coincidencia aproximada), fecha
  │    (AAAA-MM-DD, DD/MM/AAAA, «5 Mars 2026», MM/DD/AA de Walmart), TOTAL, SOUS-TOTAL, TPS, TVQ y líneas
  │    (nombre + precio, quita códigos UPC, une «2 @ $8.53» con el nombre de la línea anterior).
  │    Se elige la variante de OCR con mejor puntaje (cuadra > total > fecha > comercio > nº de líneas).
  ├─ Categorías: app/dim_categoria.json (copia de solo lectura de la hoja DimCategoria, 351 reglas).
  │    Coincidencia literal sin mayúsculas ni acentos; gana la Prioridad menor, y en empate la palabra más larga.
  │    Las palabras de 3 letras o menos deben coincidir como palabra completa. Sin coincidencia: «Sin identificar» y se marca para revisar.
  │    Categoría del recibo = la categoría con más dinero.
  ├─ Conciliación: suma de líneas + TPS + TVQ contra el total, con tolerancia de 0,02 → «Cuadra» o «Revisar».
  │    Nunca se fuerza el total: la persona corrige el nombre, el precio, la categoría, el total o los impuestos en el formulario.
  │    Si cambia el nombre o deja vacía la categoría, se recategoriza sola; si elige la categoría a mano, se respeta.
  └─ Tablero: total por mes (toca un mes para filtrar), barras por categoría con su valor (impuestos aparte),
       recibos del más nuevo al más viejo, con miniatura, y el detalle de cada uno con la foto y el texto OCR.
SQLite: data/app.db (users, receipts, lines). Cada consulta filtra por user_id.
```

Archivos clave: `app/main.py` (rutas), `app/ocr.py`, `app/parse.py`, `app/templates/*.html`,
`app/static/` (manifest, service worker, iconos, CSS y JS), `Dockerfile`.

PWA: `manifest.webmanifest` con display standalone, iconos de 192, 512 y maskable, y theme #7A4E2D.
`sw.js` guarda en caché solo la parte estática y una página «Sin conexión». Las páginas con datos siempre van a la red,
así ningún dato privado queda guardado en el caché. Para instalarla, Chrome exige HTTPS, de ahí el túnel.

## Ejecutar

### Local
```bash
pip install -r requirements.txt   # tesserocr trae Tesseract 5 empaquetado
mkdir -p tessdata && for l in eng fra osd; do curl -fsSL -o tessdata/$l.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/$l.traineddata; done
cd app && uvicorn main:app --port 8765
```

### Docker / Render
`Dockerfile` (python:3.13-slim) instala dependencias, descarga los modelos de Tesseract y arranca uvicorn en `0.0.0.0:$PORT`.

Variables de entorno:
- `PORT`: lo fija Render; por defecto 10000.
- `DATA_DIR`: SQLite y fotos; por defecto `/tmp/data`, que en el plan gratis de Render se borra en cada reinicio.
- `SECRET_KEY`: firma de las cookies; si falta, se genera una al azar.
- `HTTPS_ONLY=1`: cookie solo por HTTPS.
- `TESSDATA_DIR`: carpeta de los modelos de Tesseract.

## Pasar a producción

| Prototipo | Producción |
|---|---|
| Tesseract local + reglas propias | **API de OCR de recibos**: Google Document AI (Expense Parser), Veryfi o AWS Textract AnalyzeExpense. Devuelven comercio, fecha, total, impuestos y líneas ya estructurados, con mejor resultado en fotos difíciles (fuentes condensadas, sombras, papel arrugado). Costo aproximado de 1 a 10 centavos por recibo. Basta con cambiar `ocr_receipt()` y `parse()` por un adaptador que devuelva el mismo dict (`merchant`, `date`, `total`, `tps`, `tvq`, `items[]`). La categorización con DimCategoria y la conciliación no cambian. |
| SQLite + carpeta local | **Supabase**: Postgres con Row Level Security (`user_id = auth.uid()`), Supabase Auth (correo, Google, enlace mágico) y Storage con bucket privado y URLs firmadas para las fotos. El procesamiento iría en una Edge Function o en un worker con cola, para no bloquear la subida. |
| Sesión con cookie propia | Supabase Auth o un proveedor OAuth, con recuperación de contraseña, verificación del correo y límite de intentos. |
| Proceso síncrono (de 3 a 12 s por foto) | Cola de trabajos: la app responde «Procesando…» y avisa cuando termina. |
| Túnel trycloudflare | Dominio propio (receipt-o-matic.com) en un hosting (Fly.io, Render o Cloud Run) y, más adelante, una app Android con TWA o Capacitor para la Play Store. |
| Diccionario fijo por JSON | Diccionario por usuario más uno global; las correcciones de la persona alimentan reglas nuevas. |

Privacidad (PIPEDA y Ley 25 de Quebec): política de privacidad, consentimiento, borrado de la cuenta y de las fotos,
cifrado en reposo y retención limitada de las fotos.

## Limitaciones conocidas
- Tesseract falla con fuentes condensadas de matriz de puntos y con sombras fuertes (ver resultados en el informe).
- Las líneas de varias filas (peso o cantidad) se unen con una heurística sencilla y pueden perder líneas.
- El comercio solo sale bien cuando está en la lista de cadenas de `parse.py` o aparece claro arriba del recibo.
- Sin recuperación de contraseña, sin límite de intentos de inicio de sesión y sin token CSRF (solo cookie SameSite=Lax). Se puede borrar la cuenta desde el Tablero, en «Mi cuenta».
- Las fotos se guardan sin cifrar en el disco. En el plan gratis de Render, los datos se pierden en cada reinicio o despliegue, y el servicio se duerme tras unos 15 min sin uso (el primer acceso tarda).
- El OCR toma de 3 a 12 s por foto.

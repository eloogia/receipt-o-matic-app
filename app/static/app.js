// Receipt-o-Matic: registro del service worker + subida de fotos
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
(function () {
  const form = document.getElementById('subir');
  if (!form) return;
  const overlay = document.getElementById('cargando');
  form.querySelectorAll('input[type=file]').forEach((inp) => {
    inp.addEventListener('change', async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      overlay.hidden = false;
      let blob = f;
      try { blob = await shrink(f, 2600); } catch (e) { blob = f; }
      const fd = new FormData();
      fd.append('foto', blob, 'recibo.jpg');
      try {
        const res = await fetch('/subir', { method: 'POST', body: fd, credentials: 'same-origin' });
        if (res.redirected) { location.href = res.url; return; }
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.status);
        location.href = res.url;
      } catch (e) {
        overlay.hidden = true;
        alert('No se pudo subir la foto: ' + e.message);
      }
      inp.value = '';
    });
  });
  // Reduce fotos enormes del celular antes de subir (respeta la orientación EXIF)
  async function shrink(file, max) {
    if (!window.createImageBitmap) return file;
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const s = Math.min(1, max / Math.max(bmp.width, bmp.height));
    if (s === 1 && file.size < 4e6) return file;
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise((ok) => c.toBlob(ok, 'image/jpeg', 0.92));
  }
})();

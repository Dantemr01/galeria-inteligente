/* Galería Inteligente — frontend sin dependencias (vanilla JS). */
(() => {
  "use strict";

  const API = (window.GALERIA_CONFIG && window.GALERIA_CONFIG.apiUrl || "").replace(/\/$/, "");
  const MAX_BYTES = 5 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);

  const estado = { fotos: [], filtro: "", archivo: null, seleccionada: null, clave: leerClave() };

  // ------------------------------------------------------------ clave de acceso
  // Se guarda solo durante la sesión del navegador (se borra al cerrar la pestaña).
  function leerClave() {
    try { return sessionStorage.getItem("galeria-clave") || ""; } catch { return ""; }
  }
  function guardarClave(valor) {
    estado.clave = valor;
    try { valor ? sessionStorage.setItem("galeria-clave", valor) : sessionStorage.removeItem("galeria-clave"); } catch { /* sin almacenamiento */ }
  }
  function pedirClave(incorrecta = false) {
    $("acceso-error").classList.toggle("oculto", !incorrecta);
    $("btn-salir").classList.add("oculto");
    if (!$("acceso").open) $("acceso").showModal();
    $("clave").focus();
  }

  // ------------------------------------------------------------ utilidades
  const escapar = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function avisar(mensaje, ms = 6000) {
    const a = $("aviso");
    a.textContent = mensaje;
    a.classList.remove("oculto");
    clearTimeout(avisar.t);
    if (ms) avisar.t = setTimeout(() => a.classList.add("oculto"), ms);
  }

  // fetch con reintentos y espera exponencial: tolera fallas momentáneas de red o de la API
  async function pedir(ruta, opciones = {}, intentos = 3) {
    let ultimoError;
    for (let i = 0; i < intentos; i++) {
      try {
        const r = await fetch(API + ruta, {
          ...opciones,
          headers: { "Content-Type": "application/json", "x-clave-acceso": estado.clave, ...(opciones.headers || {}) },
        });
        const datos = await r.json().catch(() => ({}));
        if (r.ok) return datos;
        if (r.status === 401) {
          guardarClave("");
          pedirClave(true);
          throw Object.assign(new Error("Clave de acceso incorrecta"), { definitivo: true });
        }
        if (r.status === 429) { ultimoError = new Error("Hay muchas peticiones; reintentando…"); await new Promise((ok) => setTimeout(ok, 500 * 2 ** i)); continue; }
        if (r.status < 500) throw Object.assign(new Error(datos.error || `Error ${r.status}`), { definitivo: true });
        ultimoError = new Error(datos.error || `El servidor respondió ${r.status}`);
      } catch (e) {
        if (e.definitivo) throw e;
        ultimoError = e;
      }
      await new Promise((ok) => setTimeout(ok, 500 * 2 ** i));
    }
    throw ultimoError;
  }

  // ------------------------------------------------------------ salud
  async function revisarSalud() {
    const caja = $("salud");
    try {
      const r = await fetch(API + "/salud");
      const d = await r.json();
      caja.className = "salud " + (d.estado === "ok" ? "ok" : "degradado");
      $("salud-texto").textContent = d.estado === "ok" ? "Servicios operando" : "Servicio degradado";
      caja.title = Object.entries(d.componentes || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
      if (d.estado !== "ok") avisar("Algunos servicios de AWS no responden. Las fotos ya subidas siguen a salvo; inténtalo en un momento.", 0);
    } catch {
      caja.className = "salud caido";
      $("salud-texto").textContent = "Sin conexión con la API";
    }
  }

  // ------------------------------------------------------------ galería
  async function cargarFotos() {
    try {
      const d = await pedir("/fotos");
      estado.fotos = d.fotos || [];
      pintar();
    } catch (e) {
      avisar("No se pudo cargar la galería: " + e.message);
    }
  }

  function coincide(foto) {
    if (!estado.filtro) return true;
    const f = estado.filtro.toLowerCase();
    return (foto.titulo || "").toLowerCase().includes(f) ||
      (foto.etiquetas || []).some((e) => (e.nombre_es || "").toLowerCase().includes(f) || (e.nombre || "").toLowerCase().includes(f));
  }

  function pintarChips() {
    const conteo = {};
    estado.fotos.forEach((f) => (f.etiquetas || []).forEach((e) => { conteo[e.nombre_es] = (conteo[e.nombre_es] || 0) + 1; }));
    const top = Object.entries(conteo).sort((a, b) => b[1] - a[1]).slice(0, 12);
    $("chips").innerHTML = top.map(([n, c]) =>
      `<button class="chip ${estado.filtro === n ? "activo" : ""}" data-etiqueta="${escapar(n)}">${escapar(n)} · ${c}</button>`).join("");
  }

  function pintar() {
    const visibles = estado.fotos.filter(coincide);
    $("contador").textContent = estado.fotos.length;
    $("vacio").classList.toggle("oculto", estado.fotos.length > 0);
    pintarChips();
    $("rejilla").innerHTML = visibles.map((f) => {
      const etiquetas = (f.etiquetas || []).slice(0, 4).map((e) => `<span>${escapar(e.nombre_es)}</span>`).join("");
      const textoEstado = { PENDIENTE: "Analizando…", LISTA: "Listo", ERROR: "Error" }[f.estado] || f.estado;
      return `
        <article class="tarjeta" data-id="${escapar(f.foto_id)}">
          <div class="marco">
            <img loading="lazy" src="${escapar(f.url)}" alt="${escapar(f.titulo)}" >
            <span class="estado ${escapar(f.estado)} ${f.estado === "PENDIENTE" ? "cargando" : ""}">${escapar(textoEstado)}</span>
          </div>
          <div class="cuerpo">
            <h4>${escapar(f.titulo)}</h4>
            <div class="etiquetas">${etiquetas || '<span>sin etiquetas aún</span>'}</div>
          </div>
        </article>`;
    }).join("");
  }

  // ------------------------------------------------------------ detalle
  function abrirDetalle(id) {
    const f = estado.fotos.find((x) => x.foto_id === id);
    if (!f) return;
    estado.seleccionada = f;
    $("detalle-img").src = f.url;
    $("btn-reintentar").classList.toggle("oculto", f.estado === "LISTA");
    $("detalle-titulo").textContent = f.titulo;
    const fecha = new Date((f.creada_en || 0) * 1000).toLocaleString("es-MX");
    $("detalle-meta").textContent = `${fecha} · ${(f.tamano_bytes / 1024).toFixed(0)} KB · ${f.estado}`;
    $("detalle-etiquetas").innerHTML = (f.etiquetas || []).map((e) => `
      <li><div class="fila"><span>${escapar(e.nombre_es)}</span><span>${Number(e.confianza).toFixed(1)}%</span></div>
      <div class="barra-fondo"><div style="width:${Math.min(100, Number(e.confianza))}%"></div></div></li>`).join("")
      || (f.estado === "ERROR" ? "<li>El análisis falló, pero la foto está guardada. Pulsa «Reintentar análisis».</li>" : "<li>Rekognition aún no ha analizado esta foto.</li>");
    $("detalle").showModal();
  }

  // Pide a la Lambda que analice la foto con Rekognition
  async function analizar(fotoId) {
    try {
      const { foto } = await pedir(`/fotos/${fotoId}/analizar`, { method: "POST" }, 2);
      estado.fotos = estado.fotos.map((x) => (x.foto_id === fotoId ? foto : x));
    } catch (e) {
      estado.fotos = estado.fotos.map((x) => (x.foto_id === fotoId ? { ...x, estado: "ERROR" } : x));
      avisar(e.message);
    }
    pintar();
  }

  async function reintentarSeleccionada() {
    const f = estado.seleccionada;
    if (!f) return;
    $("detalle").close();
    estado.fotos = estado.fotos.map((x) => (x.foto_id === f.foto_id ? { ...x, estado: "PENDIENTE" } : x));
    pintar();
    await analizar(f.foto_id);
  }

  async function eliminarSeleccionada() {
    const f = estado.seleccionada;
    if (!f || !confirm(`¿Eliminar "${f.titulo}"?`)) return;
    try {
      await pedir(`/fotos/${f.foto_id}`, { method: "DELETE" });
      $("detalle").close();
      estado.fotos = estado.fotos.filter((x) => x.foto_id !== f.foto_id);
      pintar();
    } catch (e) {
      avisar("No se pudo eliminar: " + e.message);
    }
  }

  // ------------------------------------------------------------ subida
  function elegirArchivo(archivo) {
    if (!archivo) return;
    if (!["image/jpeg", "image/png"].includes(archivo.type)) return avisar("Solo JPG o PNG.");
    if (archivo.size > MAX_BYTES) return avisar("La imagen pesa más de 5 MB.");
    estado.archivo = archivo;
    const img = $("vista-previa");
    img.src = URL.createObjectURL(archivo);
    img.classList.remove("oculto");
    $("zona-texto").classList.add("oculto");
    $("btn-subir").disabled = false;
    if (!$("titulo").value) $("titulo").value = archivo.name.replace(/\.[^.]+$/, "");
  }

  function subirAS3(url, archivo) {
    // XHR en vez de fetch para poder mostrar el progreso de subida
    return new Promise((resolver, rechazar) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.setRequestHeader("Content-Type", archivo.type);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) $("progreso").firstElementChild.style.width = `${(e.loaded / e.total) * 100}%`;
      };
      xhr.onload = () => (xhr.status < 300 ? resolver() : rechazar(new Error(`S3 respondió ${xhr.status}`)));
      xhr.onerror = () => rechazar(new Error("Falló la conexión con S3"));
      xhr.send(archivo);
    });
  }

  async function subir(ev) {
    ev.preventDefault();
    const archivo = estado.archivo;
    if (!archivo) return;
    const boton = $("btn-subir");
    boton.disabled = true;
    boton.textContent = "Subiendo…";
    $("progreso").classList.remove("oculto");
    try {
      const { foto, uploadUrl } = await pedir("/fotos", {
        method: "POST",
        body: JSON.stringify({ titulo: $("titulo").value, contentType: archivo.type, size: archivo.size }),
      });
      await subirAS3(uploadUrl, archivo);
      estado.fotos.unshift({ ...foto, url: URL.createObjectURL(archivo) });
      pintar();
      reiniciarFormulario();
      analizar(foto.foto_id);
    } catch (e) {
      avisar("No se pudo subir: " + e.message);
      boton.disabled = false;
    } finally {
      boton.textContent = "Subir y analizar";
      $("progreso").classList.add("oculto");
      $("progreso").firstElementChild.style.width = "0";
    }
  }

  function reiniciarFormulario() {
    estado.archivo = null;
    $("form-subir").reset();
    $("vista-previa").classList.add("oculto");
    $("zona-texto").classList.remove("oculto");
    $("btn-subir").disabled = true;
  }

  // ------------------------------------------------------------ eventos
  function enlazar() {
    const zona = $("zona");
    $("archivo").addEventListener("change", (e) => elegirArchivo(e.target.files[0]));
    ["dragenter", "dragover"].forEach((t) => zona.addEventListener(t, (e) => { e.preventDefault(); zona.classList.add("encima"); }));
    ["dragleave", "drop"].forEach((t) => zona.addEventListener(t, (e) => { e.preventDefault(); zona.classList.remove("encima"); }));
    zona.addEventListener("drop", (e) => elegirArchivo(e.dataTransfer.files[0]));
    $("form-subir").addEventListener("submit", subir);
    $("buscar").addEventListener("input", (e) => { estado.filtro = e.target.value.trim(); pintar(); });
    $("chips").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      estado.filtro = estado.filtro === chip.dataset.etiqueta ? "" : chip.dataset.etiqueta;
      $("buscar").value = estado.filtro;
      pintar();
    });
    $("rejilla").addEventListener("click", (e) => {
      const t = e.target.closest(".tarjeta");
      if (t) abrirDetalle(t.dataset.id);
    });
    document.querySelector("#detalle .cerrar").addEventListener("click", () => $("detalle").close());
    $("btn-eliminar").addEventListener("click", eliminarSeleccionada);
    $("btn-reintentar").addEventListener("click", reintentarSeleccionada);
  }

  function entrar(ev) {
    ev.preventDefault();
    guardarClave($("clave").value.trim());
    $("clave").value = "";
    $("acceso").close();
    $("btn-salir").classList.remove("oculto");
    cargarFotos();
  }

  enlazar();
  $("form-acceso").addEventListener("submit", entrar);
  $("acceso").addEventListener("cancel", (e) => e.preventDefault()); // no se puede cerrar sin clave
  $("btn-salir").addEventListener("click", () => { guardarClave(""); estado.fotos = []; pintar(); pedirClave(); });
  if (!API) {
    avisar("Falta configurar la URL de la API (ejecuta python infra/desplegar.py).", 0);
    $("salud").className = "salud caido";
    $("salud-texto").textContent = "Sin configurar";
    return;
  }
  revisarSalud();
  setInterval(revisarSalud, 30000);
  if (estado.clave) { $("btn-salir").classList.remove("oculto"); cargarFotos(); } else { pedirClave(); }
})();

#!/usr/bin/env python3
"""
Pruebas de Resistencia.

Cada escenario rompe una pieza a propósito, muestra qué pasa y la repara.
La bitácora se guarda en pruebas/resultados/ como evidencia.

    python pruebas/resiliencia.py rekognition-falla --imagen perro.jpg
    python pruebas/resiliencia.py base-caida
    python pruebas/resiliencia.py carga --imagen perro.jpg --n 10
    python pruebas/resiliencia.py restaurar
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(os.path.dirname(AQUI), "salida_despliegue.json")
try:
    import boto3
except ImportError:
    sys.exit("Falta boto3: pip install -r requirements.txt")
if not os.path.exists(SALIDA):
    sys.exit("Primero despliega: python infra/desplegar.py")
with open(SALIDA, encoding="utf-8") as f:
    S = json.load(f)
lam = boto3.client("lambda", region_name=S["region"])
BITACORA = []


def log(texto):
    linea = f"[{time.strftime('%H:%M:%S')}] {texto}"
    print(linea)
    BITACORA.append(linea)


def guardar(nombre):
    os.makedirs(os.path.join(AQUI, "resultados"), exist_ok=True)
    ruta = os.path.join(AQUI, "resultados", f"{time.strftime('%Y%m%d-%H%M%S')}-{nombre}.md")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(f"# Prueba: {nombre}\n\n```\n" + "\n".join(BITACORA) + "\n```\n")
    print(f"Bitácora: {ruta}")


def http(metodo, ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(S["api_url"] + ruta, data=datos, method=metodo,
                                 headers={"Content-Type": "application/json",
                                          "x-clave-acceso": S.get("clave", "")})
    for intento in range(4):  # 429 = tope de concurrencia alcanzado: esperar y reintentar
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < 3:
                time.sleep(0.5 * 2 ** intento)
                continue
            return e.code, json.loads(e.read() or b"{}")


def subir(imagen, titulo):
    tipo = "image/png" if imagen.lower().endswith(".png") else "image/jpeg"
    contenido = open(imagen, "rb").read()
    codigo, r = http("POST", "/fotos", {"titulo": titulo, "contentType": tipo, "size": len(contenido)})
    if codigo != 201:
        raise RuntimeError(f"POST /fotos -> {codigo} {r}")
    urllib.request.urlopen(urllib.request.Request(
        r["uploadUrl"], data=contenido, method="PUT", headers={"Content-Type": tipo}), timeout=30).close()
    return r["foto"]["foto_id"]


def variables():
    return lam.get_function_configuration(FunctionName=S["lambda"])["Environment"]["Variables"]


def cambiar(**nuevas):
    lam.update_function_configuration(FunctionName=S["lambda"], Environment={"Variables": {**variables(), **nuevas}})
    lam.get_waiter("function_updated_v2").wait(FunctionName=S["lambda"])


def exigir_imagen(args):
    if not args.imagen or not os.path.exists(args.imagen):
        sys.exit("Indica una foto real: --imagen ruta/foto.jpg")


# ------------------------------------------------------------------ escenarios
def rekognition_falla(args):
    """Si Rekognition falla, la foto NO se pierde: queda en ERROR y se puede reintentar."""
    exigir_imagen(args)
    log("HIPÓTESIS: si Rekognition falla, la foto queda guardada en estado ERROR y se recupera al reintentar.")
    log("1) Simulando caída de Rekognition (SIMULAR_FALLA=true)...")
    cambiar(SIMULAR_FALLA="true")
    fid = subir(args.imagen, "Prueba de falla")
    codigo, r = http("POST", f"/fotos/{fid}/analizar")
    log(f"   Analizar -> {codigo}: {r.get('error')}")
    codigo_lista, lista = http("GET", "/fotos")
    estado = next((f["estado"] for f in lista.get("fotos", []) if f["foto_id"] == fid), "no encontrada")
    log(f"   La foto sigue en la galería con estado: {estado}")
    log("2) Reparando (SIMULAR_FALLA=false) y reintentando...")
    cambiar(SIMULAR_FALLA="false")
    codigo2, r2 = http("POST", f"/fotos/{fid}/analizar")
    etiquetas = [e["nombre_es"] for e in r2.get("foto", {}).get("etiquetas", [])]
    log(f"   Reintento -> {codigo2}, etiquetas: {etiquetas}")
    exito = codigo == 503 and estado == "ERROR" and codigo2 == 200
    log(f"RESULTADO: {'ÉXITO' if exito else 'REVISAR'} — la foto se recuperó sin volver a subirla.")
    guardar("rekognition-falla")


def base_caida(_args):
    """Si DynamoDB no responde, la API contesta 503 con mensaje claro y la web muestra un aviso."""
    log("HIPÓTESIS: sin base de datos la API no truena: responde 503 y /salud dice qué falla.")
    original = variables()["TABLA_FOTOS"]
    log("1) Apuntando la Lambda a una tabla que no existe...")
    cambiar(TABLA_FOTOS="tabla-que-no-existe")
    c1, r1 = http("GET", "/salud")
    log(f"   GET /salud -> {c1}: {r1}")
    c2, r2 = http("GET", "/fotos")
    log(f"   GET /fotos -> {c2}: {r2}")
    log("   (Abre la web ahora: debe mostrar el aviso amarillo.)")
    time.sleep(3)
    log("2) Restaurando la tabla correcta...")
    cambiar(TABLA_FOTOS=original)
    c3, _ = http("GET", "/salud")
    log(f"   GET /salud -> {c3}")
    log(f"RESULTADO: {'ÉXITO' if (c1, c2, c3) == (503, 503, 200) else 'REVISAR'} — falla controlada y recuperada.")
    guardar("base-caida")


def carga(args):
    """Muchas subidas a la vez: Lambda escala sola y todas se analizan."""
    exigir_imagen(args)
    log(f"HIPÓTESIS: {args.n} subidas simultáneas se analizan todas sin errores.")
    t0 = time.time()

    def flujo(i):
        fid = subir(args.imagen, f"Carga #{i + 1}")
        return http("POST", f"/fotos/{fid}/analizar")[0]

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        codigos = list(ex.map(flujo, range(args.n)))
    ok = codigos.count(200)
    log(f"   Códigos: {codigos}")
    log(f"RESULTADO: {ok}/{args.n} fotos analizadas en {time.time() - t0:.1f} s.")
    guardar("carga")


def restaurar(_args):
    cambiar(SIMULAR_FALLA="false", TABLA_FOTOS=S["tabla"])
    log(f"Configuración restaurada. GET /salud -> {http('GET', '/salud')}")


ESCENARIOS = {"rekognition-falla": rekognition_falla, "base-caida": base_caida,
              "carga": carga, "restaurar": restaurar}

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Pruebas de resistencia")
    p.add_argument("escenario", choices=ESCENARIOS)
    p.add_argument("--imagen")
    p.add_argument("--n", type=int, default=10)
    a = p.parse_args()
    ESCENARIOS[a.escenario](a)

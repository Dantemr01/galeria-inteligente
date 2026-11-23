"""
Lambda "galeria-api": TODO el backend de la Galería Inteligente en una sola función.

Se expone con una Lambda Function URL (no se necesita API Gateway).

    GET    /salud                -> estado de DynamoDB y S3
    GET    /fotos                -> lista de fotos (filtro opcional ?etiqueta=perro)
    POST   /fotos                -> registra una foto y devuelve una URL prefirmada para subirla a S3
    POST   /fotos/{id}/analizar  -> analiza la foto con Rekognition y guarda las etiquetas
    DELETE /fotos/{id}           -> borra la foto de S3 y DynamoDB

Seguridad:
  * Todas las rutas (excepto /salud) exigen la cabecera "x-clave-acceso" con la clave
    del equipo, guardada en la variable de entorno CLAVE_ACCESO.

Resiliencia:
  * Rekognition y DynamoDB se llaman con reintentos automáticos (modo "standard" de botocore).
  * Si el análisis falla, la foto queda en estado ERROR y se puede reintentar sin volver a subirla.
  * Si un servicio no responde, la API contesta 503 con un mensaje claro en vez de "tronar".
"""
import hmac
import json
import os
import re
import time
import uuid
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLA = os.environ.get("TABLA_FOTOS", "galeria-fotos")
BUCKET = os.environ.get("BUCKET_FOTOS", "")
MAX_BYTES = 5 * 1024 * 1024
TIPOS = {"image/jpeg": "jpg", "image/png": "png"}

_config = Config(retries={"mode": "standard", "max_attempts": 3}, signature_version="s3v4",
                 s3={"addressing_style": "virtual"})
_tabla = boto3.resource("dynamodb", region_name=REGION, config=_config).Table(TABLA)
_s3 = boto3.client("s3", region_name=REGION, config=_config)
_rekognition = boto3.client("rekognition", region_name=REGION, config=_config)

TRADUCCIONES = {
    "Person": "Persona", "Face": "Rostro", "Dog": "Perro", "Cat": "Gato", "Pet": "Mascota",
    "Animal": "Animal", "Bird": "Ave", "Plant": "Planta", "Tree": "Árbol", "Flower": "Flor",
    "Leaf": "Hoja", "Grass": "Pasto", "Vegetable": "Verdura", "Fruit": "Fruta", "Tomato": "Jitomate",
    "Food": "Comida", "Drink": "Bebida", "Car": "Auto", "Vehicle": "Vehículo", "Bicycle": "Bicicleta",
    "Building": "Edificio", "City": "Ciudad", "Street": "Calle", "House": "Casa", "Nature": "Naturaleza",
    "Outdoors": "Exterior", "Indoors": "Interior", "Sky": "Cielo", "Water": "Agua", "Beach": "Playa",
    "Mountain": "Montaña", "Computer": "Computadora", "Laptop": "Laptop", "Phone": "Teléfono",
    "Text": "Texto", "Book": "Libro", "Furniture": "Mueble", "Chair": "Silla", "Table": "Mesa",
    "Clothing": "Ropa", "Shoe": "Zapato", "Sport": "Deporte", "Ball": "Balón", "Art": "Arte",
    "Garden": "Jardín", "Potted Plant": "Planta en maceta", "Smile": "Sonrisa", "Night": "Noche",
}


class FallaSimulada(Exception):
    """Error intencional para las pruebas de resistencia."""


class _JSONDecimal(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def respuesta(codigo, cuerpo):
    return {"statusCode": codigo, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(cuerpo, cls=_JSONDecimal, ensure_ascii=False)}


def error(codigo, mensaje):
    return respuesta(codigo, {"error": mensaje})


def _con_url(item):
    item = dict(item)
    item["url"] = _s3.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": item["s3_key"]}, ExpiresIn=3600)
    return item


# ------------------------------------------------------------------ rutas
def salud():
    estado = {"api": "ok", "dynamodb": "ok", "s3": "ok"}
    try:
        _tabla.load()
    except (ClientError, BotoCoreError) as e:
        estado["dynamodb"] = f"error: {e.__class__.__name__}"
    try:
        _s3.head_bucket(Bucket=BUCKET)
    except (ClientError, BotoCoreError) as e:
        estado["s3"] = f"error: {e.__class__.__name__}"
    general = "ok" if all(v == "ok" for v in estado.values()) else "degradado"
    return respuesta(200 if general == "ok" else 503, {"estado": general, "componentes": estado})


def listar(query):
    etiqueta = (query.get("etiqueta") or "").strip().lower()
    items, kwargs = [], {}
    while True:
        pagina = _tabla.scan(**kwargs)
        items.extend(pagina.get("Items", []))
        if "LastEvaluatedKey" not in pagina:
            break
        kwargs["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]
    if etiqueta:
        items = [i for i in items if any(etiqueta in e["nombre_es"].lower() for e in i.get("etiquetas", []))]
    items.sort(key=lambda i: i.get("creada_en", 0), reverse=True)
    return respuesta(200, {"fotos": [_con_url(i) for i in items], "total": len(items)})


def crear(cuerpo):
    try:
        datos = json.loads(cuerpo or "{}")
    except json.JSONDecodeError:
        return error(400, "El cuerpo debe ser JSON válido")
    tipo = datos.get("contentType", "")
    if tipo not in TIPOS:
        return error(400, "Solo se aceptan imágenes JPG o PNG")
    tamano = int(datos.get("size") or 0)
    if not 0 < tamano <= MAX_BYTES:
        return error(400, "La imagen debe pesar máximo 5 MB")

    foto_id = uuid.uuid4().hex
    titulo = re.sub(r"[<>]", "", (datos.get("titulo") or "").strip())[:80] or "Sin título"
    item = {"foto_id": foto_id, "titulo": titulo, "s3_key": f"fotos/{foto_id}.{TIPOS[tipo]}",
            "tamano_bytes": tamano, "estado": "PENDIENTE", "etiquetas": [], "creada_en": int(time.time())}
    _tabla.put_item(Item=item)
    url = _s3.generate_presigned_url(
        "put_object", Params={"Bucket": BUCKET, "Key": item["s3_key"], "ContentType": tipo}, ExpiresIn=300)
    return respuesta(201, {"foto": item, "uploadUrl": url})


def analizar(foto_id):
    item = _tabla.get_item(Key={"foto_id": foto_id}).get("Item")
    if not item:
        return error(404, "Foto no encontrada")
    try:
        if os.environ.get("SIMULAR_FALLA", "false").lower() == "true":
            raise FallaSimulada("Falla simulada de Rekognition (prueba de resistencia)")
        r = _rekognition.detect_labels(
            Image={"S3Object": {"Bucket": BUCKET, "Name": item["s3_key"]}}, MaxLabels=10, MinConfidence=75)
    except (ClientError, BotoCoreError, FallaSimulada) as e:
        # La foto NO se pierde: queda en ERROR y el usuario puede reintentar
        print(json.dumps({"nivel": "ERROR", "foto_id": foto_id, "detalle": f"{e.__class__.__name__}: {e}"}))
        _tabla.update_item(Key={"foto_id": foto_id}, UpdateExpression="SET estado = :e",
                           ExpressionAttributeValues={":e": "ERROR"})
        return error(503, "No se pudo analizar la foto. Está guardada; puedes reintentar el análisis.")

    etiquetas = [{"nombre": e["Name"], "nombre_es": TRADUCCIONES.get(e["Name"], e["Name"]),
                  "confianza": Decimal(str(round(e["Confidence"], 2)))} for e in r.get("Labels", [])]
    _tabla.update_item(
        Key={"foto_id": foto_id}, UpdateExpression="SET estado = :e, etiquetas = :t, analizada_en = :a",
        ExpressionAttributeValues={":e": "LISTA", ":t": etiquetas, ":a": int(time.time())})
    item.update(estado="LISTA", etiquetas=etiquetas)
    return respuesta(200, {"foto": _con_url(item)})


def eliminar(foto_id):
    item = _tabla.get_item(Key={"foto_id": foto_id}).get("Item")
    if not item:
        return error(404, "Foto no encontrada")
    _s3.delete_object(Bucket=BUCKET, Key=item["s3_key"])
    _tabla.delete_item(Key={"foto_id": foto_id})
    return respuesta(200, {"eliminada": foto_id})


# ------------------------------------------------------------------ seguridad
def autorizado(evento):
    """Compara la clave recibida con la configurada en tiempo constante (evita ataques de tiempo)."""
    esperada = os.environ.get("CLAVE_ACCESO", "")
    if not esperada:  # sin clave configurada no se permite nada (falla cerrada)
        return False
    recibida = (evento.get("headers") or {}).get("x-clave-acceso", "")
    return hmac.compare_digest(recibida.encode(), esperada.encode())


# ------------------------------------------------------------------ entrada
def handler(evento, _contexto):
    metodo = evento.get("requestContext", {}).get("http", {}).get("method", "GET")
    ruta = evento.get("rawPath", "/").rstrip("/") or "/"
    query = evento.get("queryStringParameters") or {}
    m = re.fullmatch(r"/fotos/([0-9a-f]{32})(/analizar)?", ruta)
    try:
        if metodo == "GET" and ruta == "/salud":  # pública: no expone datos de usuarios
            return salud()
        if not autorizado(evento):
            print(json.dumps({"nivel": "AVISO", "ruta": ruta, "detalle": "clave de acceso incorrecta"}))
            return error(401, "Clave de acceso incorrecta")
        if metodo == "GET" and ruta == "/fotos":
            return listar(query)
        if metodo == "POST" and ruta == "/fotos":
            return crear(evento.get("body"))
        if m and metodo == "POST" and m.group(2):
            return analizar(m.group(1))
        if m and metodo == "DELETE" and not m.group(2):
            return eliminar(m.group(1))
        return error(404, f"Ruta no soportada: {metodo} {ruta}")
    except (ClientError, BotoCoreError) as e:
        print(json.dumps({"nivel": "ERROR", "ruta": ruta, "detalle": str(e)}))
        return error(503, "Un servicio de AWS no está disponible en este momento. Intenta de nuevo.")

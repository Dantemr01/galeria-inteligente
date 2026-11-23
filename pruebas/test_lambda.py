"""
Pruebas unitarias de la Lambda SIN conectarse a AWS (boto3 se reemplaza por objetos falsos).

    python -m unittest discover -s pruebas -v
"""
import importlib.util
import json
import os
import sys
import types
import unittest
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ClientError(Exception):
    def __init__(self, codigo="Error"):
        super().__init__(codigo)
        self.response = {"Error": {"Code": codigo}}


class TablaFalsa:
    def __init__(self):
        self.items, self.caida = {}, False

    def _ok(self):
        if self.caida:
            raise ClientError("ResourceNotFoundException")

    def load(self): self._ok()
    def put_item(self, Item): self._ok(); self.items[Item["foto_id"]] = dict(Item)
    def get_item(self, Key):
        self._ok()
        i = self.items.get(Key["foto_id"])
        return {"Item": dict(i)} if i else {}
    def delete_item(self, Key): self._ok(); self.items.pop(Key["foto_id"], None)
    def scan(self, **_): self._ok(); return {"Items": [dict(i) for i in self.items.values()]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self._ok()
        v, item = ExpressionAttributeValues, self.items[Key["foto_id"]]
        item["estado"] = v[":e"]
        if ":t" in v:
            item["etiquetas"] = v[":t"]


class S3Falso:
    def generate_presigned_url(self, op, Params, ExpiresIn): return f"https://s3.falso/{op}/{Params['Key']}"
    def head_bucket(self, Bucket): return {}
    def delete_object(self, Bucket, Key): pass


class RekognitionFalso:
    def __init__(self): self.caido = False

    def detect_labels(self, **_):
        if self.caido:
            raise ClientError("ThrottlingException")
        return {"Labels": [{"Name": "Dog", "Confidence": 98.123}, {"Name": "Grass", "Confidence": 80.5}]}


TABLA, S3, REKO = TablaFalsa(), S3Falso(), RekognitionFalso()

boto3 = types.ModuleType("boto3")
boto3.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda n: TABLA)
boto3.client = lambda servicio, **k: {"s3": S3, "rekognition": REKO}[servicio]
config = types.ModuleType("botocore.config")
config.Config = lambda **k: None
exc = types.ModuleType("botocore.exceptions")
exc.ClientError, exc.BotoCoreError = ClientError, type("BotoCoreError", (Exception,), {})
sys.modules.update({"boto3": boto3, "botocore": types.ModuleType("botocore"),
                    "botocore.config": config, "botocore.exceptions": exc})

os.environ.update(BUCKET_FOTOS="bucket", TABLA_FOTOS="tabla", CLAVE_ACCESO="clave-secreta")
spec = importlib.util.spec_from_file_location("app", os.path.join(RAIZ, "lambda", "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def pedir(metodo, ruta, cuerpo=None, query=None, clave="clave-secreta"):
    headers = {"x-clave-acceso": clave} if clave is not None else {}
    r = app.handler({"requestContext": {"http": {"method": metodo}}, "rawPath": ruta, "headers": headers,
                     "body": json.dumps(cuerpo) if cuerpo is not None else None,
                     "queryStringParameters": query}, None)
    return r["statusCode"], json.loads(r["body"])


class PruebasGaleria(unittest.TestCase):
    def setUp(self):
        TABLA.items.clear()
        TABLA.caida = REKO.caido = False
        os.environ["SIMULAR_FALLA"] = "false"

    def crear(self, **extra):
        return pedir("POST", "/fotos", {"titulo": "Mi perro", "contentType": "image/jpeg", "size": 1000, **extra})

    def test_crear_devuelve_url_prefirmada(self):
        codigo, r = self.crear()
        self.assertEqual(codigo, 201)
        self.assertIn("put_object/fotos/", r["uploadUrl"])
        self.assertEqual(r["foto"]["estado"], "PENDIENTE")

    def test_validaciones(self):
        self.assertEqual(self.crear(contentType="application/pdf")[0], 400)
        self.assertEqual(self.crear(size=50 * 1024 * 1024)[0], 400)
        self.assertNotIn("<", self.crear(titulo="<b>hola</b>")[1]["foto"]["titulo"])

    def test_analizar_guarda_etiquetas_en_espanol(self):
        fid = self.crear()[1]["foto"]["foto_id"]
        codigo, r = pedir("POST", f"/fotos/{fid}/analizar")
        self.assertEqual(codigo, 200)
        self.assertEqual(r["foto"]["estado"], "LISTA")
        self.assertEqual(TABLA.items[fid]["etiquetas"][0]["nombre_es"], "Perro")
        self.assertIsInstance(TABLA.items[fid]["etiquetas"][0]["confianza"], Decimal)

    def test_listar_y_filtrar(self):
        fid = self.crear()[1]["foto"]["foto_id"]
        pedir("POST", f"/fotos/{fid}/analizar")
        self.crear(titulo="Otra")
        self.assertEqual(pedir("GET", "/fotos")[1]["total"], 2)
        self.assertEqual(pedir("GET", "/fotos", query={"etiqueta": "perro"})[1]["total"], 1)

    def test_eliminar(self):
        fid = self.crear()[1]["foto"]["foto_id"]
        self.assertEqual(pedir("DELETE", f"/fotos/{fid}")[0], 200)
        self.assertEqual(pedir("DELETE", f"/fotos/{fid}")[0], 404)

    def test_ruta_desconocida_o_id_invalido(self):
        self.assertEqual(pedir("GET", "/nada")[0], 404)
        self.assertEqual(pedir("DELETE", "/fotos/../../etc")[0], 404)

    # ---------------- resiliencia
    def test_falla_de_rekognition_no_pierde_la_foto(self):
        fid = self.crear()[1]["foto"]["foto_id"]
        REKO.caido = True
        codigo, r = pedir("POST", f"/fotos/{fid}/analizar")
        self.assertEqual(codigo, 503)
        self.assertIn("reintentar", r["error"])
        self.assertEqual(TABLA.items[fid]["estado"], "ERROR")
        REKO.caido = False  # se recupera el servicio y se reintenta
        self.assertEqual(pedir("POST", f"/fotos/{fid}/analizar")[1]["foto"]["estado"], "LISTA")

    def test_falla_simulada(self):
        fid = self.crear()[1]["foto"]["foto_id"]
        os.environ["SIMULAR_FALLA"] = "true"
        self.assertEqual(pedir("POST", f"/fotos/{fid}/analizar")[0], 503)
        self.assertEqual(TABLA.items[fid]["estado"], "ERROR")

    def test_dynamodb_caida_responde_503_controlado(self):
        TABLA.caida = True
        codigo, r = pedir("GET", "/salud")
        self.assertEqual(codigo, 503)
        self.assertEqual(r["componentes"]["s3"], "ok")
        codigo, r = pedir("GET", "/fotos")
        self.assertEqual(codigo, 503)
        self.assertIn("no está disponible", r["error"])

    # ---------------- seguridad
    def test_sin_clave_o_clave_incorrecta_responde_401(self):
        self.crear()
        self.assertEqual(pedir("GET", "/fotos", clave=None)[0], 401)
        self.assertEqual(pedir("GET", "/fotos", clave="otra")[0], 401)
        self.assertEqual(pedir("POST", "/fotos", {"contentType": "image/jpeg", "size": 10}, clave="")[0], 401)

    def test_salud_es_publica_y_no_expone_fotos(self):
        codigo, r = pedir("GET", "/salud", clave=None)
        self.assertEqual(codigo, 200)
        self.assertNotIn("fotos", r)

    def test_sin_clave_configurada_se_bloquea_todo(self):
        os.environ["CLAVE_ACCESO"] = ""
        try:
            self.assertEqual(pedir("GET", "/fotos", clave="")[0], 401)
        finally:
            os.environ["CLAVE_ACCESO"] = "clave-secreta"

    def test_salud_ok(self):
        self.assertEqual(pedir("GET", "/salud"), (200, {"estado": "ok", "componentes": {
            "api": "ok", "dynamodb": "ok", "s3": "ok"}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)

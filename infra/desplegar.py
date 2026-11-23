#!/usr/bin/env python3
"""
Crea TODA la infraestructura de la Galería Inteligente con boto3 (sin usar la consola web).

Servicios: S3 (sitio web + fotos), Lambda (con Function URL), DynamoDB y Rekognition.

Uso:
    python infra/desplegar.py
    python infra/desplegar.py --region us-east-1 --prefijo galeria
    python infra/desplegar.py --rol-arn arn:aws:iam::123:role/MiRol   # cuenta que no es Academy
    python infra/desplegar.py --clave MiClaveSecreta                 # elegir la clave de acceso

La primera vez genera una clave de acceso aleatoria y la imprime al final.

Se puede ejecutar varias veces: reutiliza lo que ya existe y actualiza el código.
"""
import argparse
import io
import json
import mimetypes
import os
import secrets
import sys
import time
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, "salida_despliegue.json")

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("Falta boto3. Instálalo con:  pip install -r requirements.txt")


# ------------------------------------------------------------------ configuración
def argumentos(descripcion):
    p = argparse.ArgumentParser(description=descripcion)
    p.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    p.add_argument("--prefijo", default="galeria", help="Prefijo de los recursos (minúsculas)")
    return p


def conectar(args):
    sesion = boto3.Session(region_name=args.region)
    try:
        cuenta = sesion.client("sts").get_caller_identity()["Account"]
    except Exception as e:  # noqa: BLE001
        sys.exit("No hay credenciales de AWS válidas. En AWS Academy: Start Lab > AWS Details > "
                 f"copia las credenciales a ~/.aws/credentials\nDetalle: {e}")
    p, r = args.prefijo, args.region
    nombres = {
        "cuenta": cuenta, "region": r, "prefijo": p,
        "bucket_fotos": f"{p}-fotos-{cuenta}-{r}",
        "bucket_web": f"{p}-web-{cuenta}-{r}",
        "tabla": f"{p}-fotos",
        "lambda": f"{p}-api",
    }
    nombres["url_web"] = f"http://{nombres['bucket_web']}.s3-website-{r}.amazonaws.com"
    return sesion, nombres


def codigo(e):
    return e.response.get("Error", {}).get("Code", "")


def paso(n, texto):
    print(f"\n[{n}/5] {texto}")


def ok(texto):
    print(f"   ✔ {texto}")


# ------------------------------------------------------------------ 1. DynamoDB
def crear_tabla(sesion, n):
    ddb = sesion.client("dynamodb")
    try:
        ddb.create_table(
            TableName=n["tabla"], BillingMode="PAY_PER_REQUEST",  # se paga solo por petición
            AttributeDefinitions=[{"AttributeName": "foto_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "foto_id", "KeyType": "HASH"}],
        )
        ok(f"Tabla {n['tabla']} creada")
    except ClientError as e:
        if codigo(e) != "ResourceInUseException":
            raise
        ok(f"Tabla {n['tabla']} ya existía")
    ddb.get_waiter("table_exists").wait(TableName=n["tabla"])


# ------------------------------------------------------------------ 2. S3 fotos
def crear_bucket(s3, nombre, region):
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=nombre)
        else:
            s3.create_bucket(Bucket=nombre, CreateBucketConfiguration={"LocationConstraint": region})
        ok(f"Bucket {nombre} creado")
    except ClientError as e:
        if codigo(e) != "BucketAlreadyOwnedByYou":
            raise
        ok(f"Bucket {nombre} ya existía")
    s3.get_waiter("bucket_exists").wait(Bucket=nombre)


def crear_bucket_fotos(sesion, n):
    s3 = sesion.client("s3")
    crear_bucket(s3, n["bucket_fotos"], n["region"])
    # Privado: las fotos solo se ven con URLs prefirmadas que expiran
    s3.put_public_access_block(Bucket=n["bucket_fotos"], PublicAccessBlockConfiguration={
        k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")})
    # Versionado: una foto borrada por error se puede recuperar
    s3.put_bucket_versioning(Bucket=n["bucket_fotos"], VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_lifecycle_configuration(Bucket=n["bucket_fotos"], LifecycleConfiguration={"Rules": [{
        "ID": "borrar-versiones-viejas", "Status": "Enabled", "Filter": {"Prefix": ""},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 30}}]})
    # CORS: el navegador sube la foto directo a S3
    s3.put_bucket_cors(Bucket=n["bucket_fotos"], CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": [n["url_web"], "http://localhost:8000"],
        "AllowedMethods": ["PUT", "GET"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3000}]})
    ok("Bucket de fotos privado, versionado y con CORS")


# ------------------------------------------------------------------ 3. Lambda + Function URL
def empaquetar():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(RAIZ, "lambda", "app.py"), "app.py")
    return buffer.getvalue()


def clave_existente(sesion, n):
    """Reutiliza la clave ya configurada para no cambiarla en cada despliegue."""
    try:
        cfg = sesion.client("lambda").get_function_configuration(FunctionName=n["lambda"])
        return cfg.get("Environment", {}).get("Variables", {}).get("CLAVE_ACCESO")
    except ClientError:
        return None


def desplegar_lambda(sesion, n, rol_arn, clave):
    lam = sesion.client("lambda")
    variables = {"Variables": {"TABLA_FOTOS": n["tabla"], "BUCKET_FOTOS": n["bucket_fotos"],
                               "SIMULAR_FALLA": "false", "CLAVE_ACCESO": clave}}
    try:
        lam.get_function(FunctionName=n["lambda"])
        lam.update_function_code(FunctionName=n["lambda"], ZipFile=empaquetar())
        lam.get_waiter("function_updated_v2").wait(FunctionName=n["lambda"])
        lam.update_function_configuration(FunctionName=n["lambda"], Environment=variables)
        lam.get_waiter("function_updated_v2").wait(FunctionName=n["lambda"])
        ok(f"Lambda {n['lambda']} actualizada")
    except ClientError as e:
        if codigo(e) != "ResourceNotFoundException":
            raise
        lam.create_function(
            FunctionName=n["lambda"], Runtime="python3.12", Handler="app.handler", Role=rol_arn,
            Code={"ZipFile": empaquetar()}, Timeout=15, MemorySize=256,
            Architectures=["arm64"],  # procesador Graviton: más barato y eficiente
            Environment=variables, Description="Backend de la Galería Inteligente",
        )
        lam.get_waiter("function_active_v2").wait(FunctionName=n["lambda"])
        ok(f"Lambda {n['lambda']} creada")

    cors = {"AllowOrigins": [n["url_web"], "http://localhost:8000"],
            "AllowMethods": ["GET", "POST", "DELETE"], "AllowHeaders": ["content-type", "x-clave-acceso"],
            "MaxAge": 3600}
    try:
        url = lam.create_function_url_config(FunctionName=n["lambda"], AuthType="NONE", Cors=cors)["FunctionUrl"]
    except ClientError as e:
        if codigo(e) != "ResourceConflictException":
            raise
        url = lam.update_function_url_config(FunctionName=n["lambda"], AuthType="NONE", Cors=cors)["FunctionUrl"]

    # Permisos para que la URL sea pública (AWS pide ambos desde 2025)
    permisos = [
        {"StatementId": "url-publica", "Action": "lambda:InvokeFunctionUrl", "FunctionUrlAuthType": "NONE"},
        {"StatementId": "url-publica-invoke", "Action": "lambda:InvokeFunction", "InvokedViaFunctionUrl": True},
    ]
    for permiso in permisos:
        try:
            lam.add_permission(FunctionName=n["lambda"], Principal="*", **permiso)
        except ClientError as e:
            if codigo(e) != "ResourceConflictException":
                raise
        except Exception:  # noqa: BLE001 - boto3 antiguo sin InvokedViaFunctionUrl
            print("   ⚠ Actualiza boto3 (pip install -U boto3) si la URL responde 403")
    # Tope de concurrencia: si alguien abusa de la API, como máximo corren 5 copias a la vez
    try:
        lam.put_function_concurrency(FunctionName=n["lambda"], ReservedConcurrentExecutions=5)
        ok("Concurrencia limitada a 5 ejecuciones simultáneas")
    except ClientError as e:
        # Learner Lab ya limita la cuenta a unas 10 ejecuciones y no deja reservar menos
        print(f"   ⚠ No se pudo fijar el tope de concurrencia ({codigo(e)}); "
              "la cuenta ya tiene su propio límite")

    url = url.rstrip("/")
    ok(f"Function URL: {url}")
    return url


# ------------------------------------------------------------------ 4. Sitio web
def publicar_web(sesion, n, api_url):
    s3 = sesion.client("s3")
    crear_bucket(s3, n["bucket_web"], n["region"])
    s3.put_public_access_block(Bucket=n["bucket_web"], PublicAccessBlockConfiguration={
        k: False for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")})
    s3.put_bucket_policy(Bucket=n["bucket_web"], Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
        "Resource": f"arn:aws:s3:::{n['bucket_web']}/*"}]}))
    s3.put_bucket_website(Bucket=n["bucket_web"], WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}})

    carpeta = os.path.join(RAIZ, "web")
    with open(os.path.join(carpeta, "config.js"), "w", encoding="utf-8") as f:
        f.write(f"window.GALERIA_CONFIG = {json.dumps({'apiUrl': api_url})};\n")
    for archivo in sorted(os.listdir(carpeta)):
        tipo = mimetypes.guess_type(archivo)[0] or "application/octet-stream"
        s3.upload_file(os.path.join(carpeta, archivo), n["bucket_web"], archivo,
                       ExtraArgs={"ContentType": tipo + "; charset=utf-8", "CacheControl": "no-cache"})
    ok(f"Sitio publicado en {n['url_web']}")


# ------------------------------------------------------------------ principal
def main():
    p = argumentos("Despliega la Galería Inteligente en AWS")
    p.add_argument("--rol-arn", help="Rol para la Lambda. Default: LabRole de AWS Academy")
    p.add_argument("--clave", help="Clave de acceso a la galería. Default: la actual o una aleatoria")
    args = p.parse_args()
    inicio = time.time()
    sesion, n = conectar(args)
    rol = args.rol_arn or f"arn:aws:iam::{n['cuenta']}:role/LabRole"
    print(f"Cuenta {n['cuenta']} · región {n['region']} · rol {rol}")

    paso(1, "DynamoDB")
    crear_tabla(sesion, n)
    paso(2, "S3 para fotos")
    crear_bucket_fotos(sesion, n)
    paso(3, "Lambda con Function URL")
    clave = args.clave or clave_existente(sesion, n) or secrets.token_urlsafe(9)
    api_url = desplegar_lambda(sesion, n, rol, clave)
    paso(4, "Sitio web en S3")
    publicar_web(sesion, n, api_url)
    paso(5, "Guardando datos del despliegue")
    n["api_url"] = api_url
    n["clave"] = clave  # el archivo está en .gitignore: la clave no se sube a GitHub
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(n, f, indent=2)
    ok("salida_despliegue.json")

    print(f"\nListo en {int(time.time() - inicio)} s")
    print(f"  Sitio web: {n['url_web']}")
    print(f"  API:       {api_url}/salud")
    print(f"  Clave de acceso: {clave}   (compártela solo con tu equipo)")


if __name__ == "__main__":
    main()

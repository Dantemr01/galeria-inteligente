#!/usr/bin/env python3
"""Borra TODOS los recursos de la Galería Inteligente (incluidas las fotos).

Uso:  python infra/destruir.py        (pide confirmación)
      python infra/destruir.py --si
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desplegar import SALIDA, ClientError, argumentos, conectar  # noqa: E402


def intentar(texto, funcion, **kwargs):
    try:
        funcion(**kwargs)
        print(f"   ✔ {texto}")
    except ClientError as e:
        print(f"   - {texto}: omitido ({e.response['Error']['Code']})")


def vaciar_y_borrar(s3, Bucket):
    b = s3.Bucket(Bucket)
    b.object_versions.delete()
    b.delete()


def main():
    p = argumentos("Elimina la Galería Inteligente")
    p.add_argument("--si", action="store_true")
    args = p.parse_args()
    sesion, n = conectar(args)
    if not args.si and input(f"Se borrará todo '{n['prefijo']}'. Escribe 'borrar': ").strip() != "borrar":
        sys.exit("Cancelado.")

    lam, s3, ddb = sesion.client("lambda"), sesion.resource("s3"), sesion.client("dynamodb")
    intentar("Function URL", lam.delete_function_url_config, FunctionName=n["lambda"])
    intentar(f"Lambda {n['lambda']}", lam.delete_function, FunctionName=n["lambda"])
    intentar("Logs de la Lambda", sesion.client("logs").delete_log_group, logGroupName=f"/aws/lambda/{n['lambda']}")
    for bucket in (n["bucket_fotos"], n["bucket_web"]):
        intentar(f"Bucket {bucket}", vaciar_y_borrar, s3=s3, Bucket=bucket)
    intentar(f"Tabla {n['tabla']}", ddb.delete_table, TableName=n["tabla"])
    if os.path.exists(SALIDA):
        os.remove(SALIDA)
    print("Listo. No quedan recursos cobrando.")


if __name__ == "__main__":
    main()

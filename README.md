# Galería Inteligente en AWS 

Aplicación web donde subes una foto y **Amazon Rekognition la etiqueta sola** (perro, planta,
auto…). Las etiquetas se traducen al español y puedes buscar tus fotos por etiqueta.

> Proyecto final del módulo **Computación Sustentable** — Universidad de Guadalajara, CUTonalá.

Usa solo **4 servicios de AWS** y toda la infraestructura se crea con un script de Python (boto3).

## Arquitectura

```
 Navegador ──1. carga la web──▶ S3 (sitio web)
     │
     ├──2. API (Function URL)──▶ Lambda ──▶ DynamoDB   (fotos y etiquetas)
     │                             │
     ├──3. sube la foto (PUT)──▶ S3 (fotos, privado)
     │                             │
     └──4. "analizar"──────────▶ Lambda ──▶ Rekognition (detecta etiquetas)
```

| Servicio | Función |
|---|---|
| **S3** | Un bucket público para la página web y otro privado y versionado para las fotos |
| **Lambda** | Todo el backend en una función, expuesta con una Function URL |
| **DynamoDB** | Guarda título, estado y etiquetas de cada foto |
| **Rekognition** | Detecta qué hay en cada foto |

## Estructura

```
infra/desplegar.py     crea todo        infra/destruir.py   borra todo
lambda/app.py          backend          web/                página (HTML, CSS, JS)
pruebas/test_lambda.py 13 pruebas unitarias (sin AWS)
pruebas/resiliencia.py pruebas de resistencia contra AWS real
```

## Guía de despliegue

1. **Requisitos:** Python 3.10 o superior, Git y acceso a AWS Academy Learner Lab.
2. **Instalar:**
   ```bash
   git clone https://github.com/Dantemr01/galeria-inteligente.git
   cd galeria-inteligente
   pip install -r requirements.txt
   ```
3. **Credenciales:** en el Learner Lab pulsa *Start Lab*, luego *AWS Details* y después *AWS CLI: Show*.
   Copia el bloque a `~/.aws/credentials` (Windows: `C:\Users\<tú>\.aws\credentials`).
4. **Desplegar:**
   ```bash
   python infra/desplegar.py
   ```
   En menos de un minuto imprime la URL del sitio web y la **clave de acceso** de la galería
   (también queda en `salida_despliegue.json`, que no se sube a GitHub). Para elegir tu propia
   clave: `python infra/desplegar.py --clave MiClave`.
5. **Probar:** abre la URL, escribe la clave de acceso, sube una foto y en unos segundos aparecen sus etiquetas.
6. **Borrar todo al terminar:** `python infra/destruir.py`

Las credenciales del Learner Lab caducan en unas 4 h. Si aparece `ExpiredToken`, repite el paso 3.

HEAD
El Script de Interacción con los servicios en AWS: infra/desplegar.py (crea toda la infraestructura con boto3) y para borrarla: infra/destruir.py

## Seguridad

- La galería pide una **clave de acceso**; la Lambda rechaza con 401 toda petición sin ella (excepto `/salud`).
- La Lambda tiene un **tope de 5 ejecuciones simultáneas** para limitar abusos y costos.
- El bucket de fotos es privado y cifrado; las fotos solo se ven con URLs que expiran en 1 hora.
  bb795f1 (Seguridad: clave de acceso y tope de concurrencia)

## Pruebas

```bash
python -m unittest discover -s pruebas -v                  # unitarias, sin AWS
python pruebas/resiliencia.py rekognition-falla --imagen perro.jpg
python pruebas/resiliencia.py base-caida
python pruebas/resiliencia.py carga --imagen perro.jpg --n 10
python pruebas/resiliencia.py restaurar
```

## Equipo
- Dante Yael Macías Rentería
- Carlos Alberto Larios Hernandez

Profesor: Víctor Hugo Vega Fregoso

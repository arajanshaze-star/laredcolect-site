# LaRedColect Autopilot v3

Integrado desde LaRedColect-Autopilot-v3.zip en una rama de revisión.
La web y las automatizaciones ParteBot y sitemap existentes se conservan.

## Estado de esta instalación

Los dos workflows nuevos son exclusivamente manuales y de simulación. No tienen
cron, parámetros para ejecución real ni acceso a secretos. No se han autorizado
YouTube, Higgsfield ni OpenAI, ni verificado las tarifas o los endpoints del ZIP.
Las URLs de los vídeos tampoco se han descargado durante las pruebas.

Los scripts bloquean publicaciones y llamadas de pago mediante
content/authorizations.json (todos los valores false) y las variables de entorno
LRC_PUBLICATION_ENABLED y LRC_PRODUCTION_ENABLED. El bloqueo se comprueba también
en las funciones de subida, generación de vídeo y narración. No basta con tener claves.

## Pruebas sin servicios externos

    python -m pip install -r requirements-dev.txt
    python -m pytest -q
    python scripts/publish.py --dry-run
    python scripts/produce.py --dry-run

La cola original contiene tres vídeos; el banco de guiones contiene dos historias.
Se conservan sus metadatos de aprobación del ZIP, pero no equivalen a la
autorización de activar el sistema. Revisar contenido, derechos y visibilidad
antes de publicar. Las estimaciones del ZIP no son límites reales de facturación.

## Continuación con YouTube

1. Revisar y fusionar este PR.
2. Configurar OAuth YouTube fuera del chat y del repositorio. El asistente local
   scripts/authorize.py guarda credenciales en .local/, excluido de Git.
3. Guardar YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET y YOUTUBE_REFRESH_TOKEN como
   secretos. Verificar en modo de lectura el canal esperado
   UCPV98kjwLUta0vuiy3fx64A y los permisos concedidos. Esa verificación no se ha hecho.
4. Revisar la cola y autorizar expresamente una primera subida privada antes de
   activar cualquier publicación pública.
5. Para producción, verificar primero la documentación y compatibilidad de
   Higgsfield y OpenAI, credenciales propias y límites de facturación del proveedor.
   MAX_ESTIMATED_VIDEO_USD solo compara una estimación; no limita la factura.
6. Registrar las verificaciones en content/authorizations.json mediante otro PR,
   activar las variables correspondientes y añadir ejecución real a los workflows
   solo después de aprobarla. Esta instalación no contiene disparadores reales.

La producción del ZIP genera y sube en una misma ejecución; --preview-only usa un
directorio temporal y no conserva el resultado. Antes de producción desatendida,
añadir conservación de resultados y un registro durable de generaciones para
evitar volver a gastar tras una generación correcta seguida de fallo de subida.
Las marcas LRC-ID permiten detectar subidas previas, no generaciones pagadas fallidas.
No activar reintentos automáticos de producción sin resolver ese punto.

No se han cambiado secretos ni variables del repositorio.

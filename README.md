# Agente de CV — Christian Martínez

Este es mi proyecto para el Reto IA Banorte. Es un agente que contesta en español preguntas sobre mi perfil: experiencia, estudios, habilidades y proyectos. Solo usa lo que está en mi CV; si algo no aparece ahí, lo dice.

Se conecta a la plataforma del reto con una URL y una clave. Sigue el formato [Open Responses](https://www.openresponses.org): recibe las preguntas en `POST /v1/responses` y puede contestar de golpe o ir mandando el texto mientras se genera.

## Cómo está organizado

```
agente/          el servicio: recibe preguntas, arma el prompt, aplica las reglas y llama al modelo
datos/cv.yaml    mi CV en formato estructurado; es lo único que el agente sabe de mí
pruebas/         pruebas del código que corren sin internet
evaluaciones/    preguntas de prueba que se le hacen al agente ya publicado
scripts/         preparar Google Cloud, publicar el agente y conectar GitHub
.github/         revisión y publicación automáticas
Dockerfile · cloudbuild.yaml · pyproject.toml · uv.lock
```

## Con qué está hecho y dónde corre

- **Código:** Python 3.12 con FastAPI. No guarda conversaciones: la plataforma manda el historial completo en cada pregunta.
- **Modelo:** Gemini 3.5 Flash en Vertex AI de Google. Le paso el CV completo en cada pregunta; como es corto, no hace falta un buscador de documentos.
- **Dónde corre:** Cloud Run de Google Cloud, en Estados Unidos (`us-central1`). Se apaga cuando nadie lo usa y sube hasta 3 copias si hay mucha carga. Las claves están en Secret Manager y los registros en Cloud Logging.
- **Publicación:** con GitHub Actions. Cada cambio se publica primero sin recibir usuarios, se evalúa, y solo pasa a producción si aprueba.

## Cómo lo probé

- **45 pruebas del código:** que respete el formato de la plataforma, que pida la clave, que ponga límites, que rechace datos mal formados y que maneje bien los errores.
- **27 preguntas de evaluación al agente real:** datos del CV, conversación de varios turnos, preguntas trampa, temas que no tienen que ver, intentos de manipularlo, preguntas en otro idioma y compromisos a nombre mío. Hay 9 casos obligatorios: si alguno falla, no se publica. En la última corrida pasaron las 27.

## Seguridad

- **Acceso:** solo contesta a quien manda una clave válida. Las claves se pueden cambiar sin apagar el servicio.
- **Abuso y costo:** límite de preguntas por minuto, límite de tamaño por mensaje, tope de respuesta y tope diario de uso del modelo. También hay alertas de gasto.
- **Manipulación:** detecta frases típicas de ataque antes de llamar al modelo, sus reglas tienen prioridad sobre lo que escriba el usuario y hay pruebas específicas para esos casos.
- **Datos inventados:** solo contesta con lo que dice el CV y avisa cuando algo no está.
- **Nube:** cada parte tiene solo los permisos que necesita, el contenedor no corre como administrador, las versiones de las dependencias están fijas y no hay ninguna clave en el repositorio.

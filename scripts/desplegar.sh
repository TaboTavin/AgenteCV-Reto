#!/usr/bin/env bash
# Uso: ID_PROYECTO=... [IMAGEN=...] [SIN_TRAFICO=1 ETIQUETA=candidata] [MIN_INSTANCIAS=1] ./scripts/desplegar.sh
set -euo pipefail

: "${ID_PROYECTO:?Define ID_PROYECTO}"
REGION="${REGION:-us-central1}"
SERVICIO="${SERVICIO:-cv-agent}"
MODELO="${MODELO_GEMINI:-gemini-3.5-flash}"

if [[ -z "${IMAGEN:-}" ]]; then
  IMAGEN="${REGION}-docker.pkg.dev/${ID_PROYECTO}/${SERVICIO}/${SERVICIO}:$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
  echo "==> Construyendo ${IMAGEN}"
  gcloud builds submit . --project="$ID_PROYECTO" --region="$REGION" --config=cloudbuild.yaml \
    --substitutions="_IMAGEN=${IMAGEN}" \
    --service-account="projects/${ID_PROYECTO}/serviceAccounts/${SERVICIO}-build@${ID_PROYECTO}.iam.gserviceaccount.com"
fi

adicionales=()
if gcloud run services describe "$SERVICIO" --project="$ID_PROYECTO" --region="$REGION" >/dev/null 2>&1; then
  [[ -n "${SIN_TRAFICO:-}" ]] && adicionales+=(--no-traffic)
  [[ -n "${ETIQUETA:-}" ]] && adicionales+=(--tag="$ETIQUETA")
else
  # La app autentica con Bearer; allUsers está bloqueado por Domain Restricted Sharing.
  adicionales+=(--no-invoker-iam-check)
fi

echo "==> Desplegando ${SERVICIO} en ${REGION}"
gcloud run deploy "$SERVICIO" \
  --project="$ID_PROYECTO" \
  --region="$REGION" \
  --image="$IMAGEN" \
  --service-account="${SERVICIO}-runtime@${ID_PROYECTO}.iam.gserviceaccount.com" \
  --set-secrets="CLAVES_API=${SERVICIO}-api-keys:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${ID_PROYECTO},GOOGLE_CLOUD_LOCATION=global,MODELO_GEMINI=${MODELO}" \
  --cpu=1 --memory=512Mi --cpu-boost \
  --concurrency=40 --min-instances="${MIN_INSTANCIAS:-0}" --max-instances=3 \
  --timeout=120 \
  --quiet \
  ${adicionales[@]+"${adicionales[@]}"}

URL=$(gcloud run services describe "$SERVICIO" --project="$ID_PROYECTO" --region="$REGION" --format="value(status.url)")
echo
echo "URL base para la plataforma: ${URL}/v1"
echo "API key: gcloud secrets versions access latest --secret=${SERVICIO}-api-keys --project=${ID_PROYECTO}"

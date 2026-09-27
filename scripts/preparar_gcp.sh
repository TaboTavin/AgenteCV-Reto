#!/usr/bin/env bash
# Uso: ID_PROYECTO=cv-agent-xxxx CUENTA_FACTURACION=XXXXXX-XXXXXX-XXXXXX ./scripts/preparar_gcp.sh
set -euo pipefail

: "${ID_PROYECTO:?Define ID_PROYECTO}"
REGION="${REGION:-us-central1}"
SERVICIO="${SERVICIO:-cv-agent}"
CUENTA_FACTURACION="${CUENTA_FACTURACION:-}"

SA_EJECUCION="${SERVICIO}-runtime@${ID_PROYECTO}.iam.gserviceaccount.com"
SA_CONSTRUCCION="${SERVICIO}-build@${ID_PROYECTO}.iam.gserviceaccount.com"
SA_DESPLIEGUE="${SERVICIO}-deployer@${ID_PROYECTO}.iam.gserviceaccount.com"
SECRETO="${SERVICIO}-api-keys"
SECRETO_CI="${SERVICIO}-clave-ci"
BUCKET_FUENTES="gs://${ID_PROYECTO}_cloudbuild"
ROL_INVOCADOR="projects/${ID_PROYECTO}/roles/invocadorGemini"

silencioso() { "$@" >/dev/null 2>&1; }
# Las service accounts recién creadas tardan unos segundos en propagarse en IAM.
reintentar() { for _ in 1 2 3 4 5 6; do "$@" >/dev/null && return 0; sleep 5; done; "$@"; }

echo "==> Proyecto ${ID_PROYECTO}"
silencioso gcloud projects describe "$ID_PROYECTO" || gcloud projects create "$ID_PROYECTO" --name="CV Agent"
gcloud config set project "$ID_PROYECTO" >/dev/null

if [[ -n "$CUENTA_FACTURACION" ]]; then
  echo "==> Facturación ${CUENTA_FACTURACION}"
  gcloud billing projects link "$ID_PROYECTO" --billing-account="$CUENTA_FACTURACION" >/dev/null
fi

echo "==> APIs"
gcloud services enable \
  run.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  billingbudgets.googleapis.com \
  containerscanning.googleapis.com

echo "==> Service accounts"
for nombre in runtime build deployer; do
  silencioso gcloud iam service-accounts describe "${SERVICIO}-${nombre}@${ID_PROYECTO}.iam.gserviceaccount.com" \
    || gcloud iam service-accounts create "${SERVICIO}-${nombre}" --display-name="${SERVICIO} ${nombre}"
done

asignar_rol() {
  reintentar gcloud projects add-iam-policy-binding "$ID_PROYECTO" \
    --member="serviceAccount:$1" --role="$2" --condition=None
}
silencioso gcloud iam roles describe invocadorGemini --project="$ID_PROYECTO" \
  || gcloud iam roles create invocadorGemini --project="$ID_PROYECTO" --title="Invocador de Gemini" \
       --permissions=aiplatform.endpoints.predict --stage=GA
asignar_rol "$SA_EJECUCION" "$ROL_INVOCADOR"
asignar_rol "$SA_CONSTRUCCION" roles/logging.logWriter
asignar_rol "$SA_DESPLIEGUE" roles/run.developer
reintentar gcloud iam service-accounts add-iam-policy-binding "$SA_EJECUCION" \
  --member="serviceAccount:${SA_DESPLIEGUE}" --role=roles/iam.serviceAccountUser

silencioso gcloud storage buckets describe "$BUCKET_FUENTES" \
  || gcloud storage buckets create "$BUCKET_FUENTES" --location=us --uniform-bucket-level-access
reintentar gcloud storage buckets add-iam-policy-binding "$BUCKET_FUENTES" \
  --member="serviceAccount:${SA_CONSTRUCCION}" --role=roles/storage.objectViewer

echo "==> Artifact Registry"
silencioso gcloud artifacts repositories describe "$SERVICIO" --location="$REGION" \
  || gcloud artifacts repositories create "$SERVICIO" --repository-format=docker --location="$REGION" \
       --description="Imágenes del agente de CV"
for sa in "$SA_CONSTRUCCION" "$SA_DESPLIEGUE"; do
  reintentar gcloud artifacts repositories add-iam-policy-binding "$SERVICIO" --location="$REGION" \
    --member="serviceAccount:${sa}" --role=roles/artifactregistry.writer
done

echo "==> Secret Manager (${SECRETO}, ${SECRETO_CI})"
nueva_clave() { python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')"; }
if ! silencioso gcloud secrets describe "$SECRETO"; then
  nueva_clave | gcloud secrets create "$SECRETO" --replication-policy=automatic --data-file=-
fi
# La key de CI vive en su propio secreto y también se acepta en producción; el deployer solo puede leer esa.
if ! silencioso gcloud secrets describe "$SECRETO_CI"; then
  clave_ci=$(nueva_clave)
  printf %s "$clave_ci" | gcloud secrets create "$SECRETO_CI" --replication-policy=automatic --data-file=-
  printf %s "$(gcloud secrets versions access latest --secret="$SECRETO"),${clave_ci}" \
    | gcloud secrets versions add "$SECRETO" --data-file=- >/dev/null
  unset clave_ci
fi
reintentar gcloud secrets add-iam-policy-binding "$SECRETO" \
  --member="serviceAccount:${SA_EJECUCION}" --role=roles/secretmanager.secretAccessor
reintentar gcloud secrets add-iam-policy-binding "$SECRETO_CI" \
  --member="serviceAccount:${SA_DESPLIEGUE}" --role=roles/secretmanager.secretAccessor

echo "==> Retirando permisos amplios de versiones anteriores"
silencioso gcloud projects remove-iam-policy-binding "$ID_PROYECTO" \
  --member="serviceAccount:${SA_EJECUCION}" --role=roles/aiplatform.user --condition=None || true
silencioso gcloud projects remove-iam-policy-binding "$ID_PROYECTO" \
  --member="serviceAccount:${SA_CONSTRUCCION}" --role=roles/storage.objectViewer --condition=None || true
silencioso gcloud secrets remove-iam-policy-binding "$SECRETO" \
  --member="serviceAccount:${SA_DESPLIEGUE}" --role=roles/secretmanager.secretAccessor || true

if [[ -n "$CUENTA_FACTURACION" ]]; then
  echo "==> Presupuesto"
  if ! gcloud billing budgets list --billing-account="$CUENTA_FACTURACION" --format="value(displayName)" \
      | grep -qx "${SERVICIO}-budget"; then
    moneda=$(gcloud billing accounts describe "$CUENTA_FACTURACION" --format="value(currencyCode)")
    monto_por_defecto=$([[ "$moneda" == "MXN" ]] && echo 400 || echo 20)
    gcloud billing budgets create --billing-account="$CUENTA_FACTURACION" \
      --display-name="${SERVICIO}-budget" \
      --budget-amount="${MONTO_PRESUPUESTO:-$monto_por_defecto}${moneda}" \
      --filter-projects="projects/${ID_PROYECTO}" \
      --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 >/dev/null
  fi
fi

echo
echo "Listo. Siguiente paso: ID_PROYECTO=${ID_PROYECTO} ./scripts/desplegar.sh"

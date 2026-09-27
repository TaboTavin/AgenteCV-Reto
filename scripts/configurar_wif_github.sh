#!/usr/bin/env bash
# Uso: ID_PROYECTO=... REPO_GITHUB=usuario/repo ./scripts/configurar_wif_github.sh
set -euo pipefail

: "${ID_PROYECTO:?Define ID_PROYECTO}"
: "${REPO_GITHUB:?Define REPO_GITHUB (usuario/repo)}"
SERVICIO="${SERVICIO:-cv-agent}"
POOL="github"
PROVEEDOR="github-oidc"
SA_DESPLIEGUE="${SERVICIO}-deployer@${ID_PROYECTO}.iam.gserviceaccount.com"
NUMERO_PROYECTO=$(gcloud projects describe "$ID_PROYECTO" --format="value(projectNumber)")

gcloud iam workload-identity-pools describe "$POOL" --project="$ID_PROYECTO" --location=global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL" --project="$ID_PROYECTO" --location=global \
       --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers describe "$PROVEEDOR" --project="$ID_PROYECTO" --location=global \
    --workload-identity-pool="$POOL" >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc "$PROVEEDOR" --project="$ID_PROYECTO" \
       --location=global --workload-identity-pool="$POOL" \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
       --attribute-condition="assertion.repository=='${REPO_GITHUB}' && assertion.ref=='refs/heads/main'"

gcloud iam service-accounts add-iam-policy-binding "$SA_DESPLIEGUE" --project="$ID_PROYECTO" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${NUMERO_PROYECTO}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO_GITHUB}" \
  >/dev/null

echo
echo "Variables de GitHub Actions (Settings → Secrets and variables → Actions → Variables):"
echo "  ID_PROYECTO_GCP = ${ID_PROYECTO}"
echo "  PROVEEDOR_WIF   = projects/${NUMERO_PROYECTO}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVEEDOR}"
echo "  SA_DESPLIEGUE   = ${SA_DESPLIEGUE}"

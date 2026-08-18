#!/usr/bin/env bash

set -Eeuo pipefail

service_id="${1:?Railway service ID is required}"
expected_config_file="${2:?Expected Railway config path is required}"
release_sha="${3:?Full release commit SHA is required}"
release_component="${4:?Release component label is required}"

: "${RAILWAY_PROJECT_ID:?RAILWAY_PROJECT_ID is required}"
: "${RAILWAY_PRODUCTION_ENVIRONMENT_ID:?RAILWAY_PRODUCTION_ENVIRONMENT_ID is required}"

railway_bin="${RAILWAY_BIN:-railway}"
timeout_seconds="${RAILWAY_DEPLOY_TIMEOUT_SECONDS:-1800}"
poll_seconds="${RAILWAY_DEPLOY_POLL_SECONDS:-10}"
release_message="ExpenseOps production ${release_component} ${release_sha}"

if [[ ! "${release_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Release SHA must be a full lowercase 40-character Git commit SHA." >&2
  exit 2
fi
case "${release_component}:${expected_config_file}" in
  migrations:/railway.migrations.json \
    |outbox:/railway.outbox.json \
    |classification-finalizer:/railway.classification-finalizer.json \
    |gmail-receipts:/railway.gmail-receipts.json \
    |gmail-promotions:/railway.gmail-promotions.json \
    |web:/railway.web.json)
    ;;
  *)
    echo "Release component and Railway config path do not match an approved service." >&2
    exit 2
    ;;
esac
local_config_file=".${expected_config_file}"
if [[ ! -f "${local_config_file}" ]]; then
  echo "Reviewed Railway config ${local_config_file} is missing from the release checkout." >&2
  exit 2
fi
expected_manifest="$(jq -c '
  {
    startCommand: (.deploy.startCommand // ""),
    preDeployCommand: (
      (.deploy.preDeployCommand // [])
      | if type == "array" then . else [.] end
    ),
    healthcheckPath: (.deploy.healthcheckPath // ""),
    healthcheckTimeout: (
      if (.deploy.healthcheckPath // "") == "" then null
      else (.deploy.healthcheckTimeout // null)
      end
    ),
    restartPolicyType: (.deploy.restartPolicyType // ""),
    restartPolicyMaxRetries: (
      if (.deploy.restartPolicyType // "") == "ON_FAILURE" then
        (.deploy.restartPolicyMaxRetries // null)
      else null
      end
    )
  }
' "${local_config_file}")"
if [[ ! "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] \
  || [[ ! "${poll_seconds}" =~ ^[0-9]+$ ]]; then
  echo "Railway deployment timeout must be positive and poll interval non-negative." >&2
  exit 2
fi

# Config-as-code selection is a dashboard setting, not a `railway up` flag. Check
# it immediately before uploading so a migration service can never fall back to
# the neutral config and start the Dockerfile's web command with migrator access.
environment_config="$("${railway_bin}" environment config \
  --environment "${RAILWAY_PRODUCTION_ENVIRONMENT_ID}" \
  --json)"
configured_path="$(jq -r --arg id "${service_id}" \
  '.services[$id].configFile // ""' <<<"${environment_config}")"
if [[ "${configured_path}" != "${expected_config_file}" ]]; then
  unset environment_config
  echo "Service ${service_id} is configured with ${configured_path:-no config file}; expected ${expected_config_file}." >&2
  exit 1
fi
if ! jq --exit-status --arg id "${service_id}" --arg component "${release_component}" '
  .privateNetworkDisabled != true
  and (.services[$id].source.repo == null)
  and (.services[$id].source.image == null)
  and (
    if $component == "web" then
      (
        ((.services[$id].networking.serviceDomains // {}) | length)
        + ((.services[$id].networking.customDomains // {}) | length)
      ) > 0
      and ((.services[$id].networking.tcpProxies // {}) | length) == 0
    else
      ((.services[$id].networking.serviceDomains // {}) | length) == 0
      and ((.services[$id].networking.customDomains // {}) | length) == 0
      and ((.services[$id].networking.tcpProxies // {}) | length) == 0
    end
  )
  and (
    if $component == "gmail-receipts" or $component == "gmail-promotions" then
      ((.services[$id].deploy.cronSchedule // "") | length) > 0
    else
      true
    end
  )
' <<<"${environment_config}" >/dev/null
then
  unset environment_config
  echo "Service ${service_id} failed the source, networking, or schedule preflight." >&2
  exit 1
fi
unset environment_config

before_deployments="$("${railway_bin}" deployment list \
  --project "${RAILWAY_PROJECT_ID}" \
  --environment "${RAILWAY_PRODUCTION_ENVIRONMENT_ID}" \
  --service "${service_id}" \
  --limit 100 \
  --json)"
before_ids="$(jq -c '[.[].id]' <<<"${before_deployments}")"

"${railway_bin}" up \
  --project "${RAILWAY_PROJECT_ID}" \
  --environment "${RAILWAY_PRODUCTION_ENVIRONMENT_ID}" \
  --service "${service_id}" \
  --detach \
  --json \
  --message "${release_message}"

deadline=$((SECONDS + timeout_seconds))
deployment_id=""

while (( SECONDS < deadline )); do
  deployments="$("${railway_bin}" deployment list \
    --project "${RAILWAY_PROJECT_ID}" \
    --environment "${RAILWAY_PRODUCTION_ENVIRONMENT_ID}" \
    --service "${service_id}" \
    --limit 100 \
    --json)"

  if [[ -z "${deployment_id}" ]]; then
    deployment_id="$(jq -r \
      --arg message "${release_message}" \
      --argjson before "${before_ids}" \
      '[.[]
        | select((.meta.cliMessage // .meta.commitMessage // "") == $message)
        | select(.id as $id | ($before | index($id)) == null)
      ][0].id // empty' <<<"${deployments}")"
  fi

  if [[ -z "${deployment_id}" ]]; then
    sleep "${poll_seconds}"
    continue
  fi

  deployment="$(jq -c --arg id "${deployment_id}" '.[] | select(.id == $id)' \
    <<<"${deployments}")"
  status="$(jq -r '.status // "UNKNOWN"' <<<"${deployment}")"

  case "${status}" in
    SUCCESS)
      config_file="$(jq -r '.meta.configFile // ""' <<<"${deployment}")"
      commit_message="$(jq -r '.meta.cliMessage // .meta.commitMessage // ""' \
        <<<"${deployment}")"
      deployed_manifest="$(jq -c '
        {
          startCommand: (.meta.serviceManifest.deploy.startCommand // ""),
          preDeployCommand: (
            (.meta.serviceManifest.deploy.preDeployCommand // [])
            | if type == "array" then . else [.] end
          ),
          healthcheckPath: (.meta.serviceManifest.deploy.healthcheckPath // ""),
          healthcheckTimeout: (
            if (.meta.serviceManifest.deploy.healthcheckPath // "") == "" then null
            else (.meta.serviceManifest.deploy.healthcheckTimeout // null)
            end
          ),
          restartPolicyType: (.meta.serviceManifest.deploy.restartPolicyType // ""),
          restartPolicyMaxRetries: (
            if (.meta.serviceManifest.deploy.restartPolicyType // "") == "ON_FAILURE" then
              (.meta.serviceManifest.deploy.restartPolicyMaxRetries // null)
            else null
            end
          )
        }
      ' <<<"${deployment}")"
      if [[ "${config_file}" != "${expected_config_file}" ]]; then
        echo "Deployment ${deployment_id} used ${config_file:-no config file}; expected ${expected_config_file}." >&2
        exit 1
      fi
      if [[ "${commit_message}" != "${release_message}" ]]; then
        echo "Deployment ${deployment_id} does not match the requested release." >&2
        exit 1
      fi
      if [[ "${deployed_manifest}" != "${expected_manifest}" ]]; then
        echo "Deployment ${deployment_id} did not apply the reviewed service command manifest." >&2
        exit 1
      fi
      echo "${release_component} deployment ${deployment_id} reached SUCCESS."
      exit 0
      ;;
    FAILED|CRASHED)
      echo "${release_component} deployment ${deployment_id} ended with ${status}." >&2
      exit 1
      ;;
    NEEDS_APPROVAL|SLEEPING|SKIPPED|REMOVED|REMOVING)
      echo "${release_component} deployment ${deployment_id} stopped in ${status}." >&2
      exit 1
      ;;
    QUEUED|INITIALIZING|WAITING|BUILDING|DEPLOYING)
      sleep "${poll_seconds}"
      ;;
    *)
      echo "${release_component} deployment ${deployment_id} returned unknown status ${status}." >&2
      exit 1
      ;;
  esac
done

echo "Timed out waiting for ${release_component} deployment ${deployment_id:-to appear}." >&2
exit 1

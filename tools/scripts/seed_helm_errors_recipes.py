"""Direct-insert helm error + recipe claims."""

from datetime import datetime, timezone
from hashlib import sha256

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import get_claim_store
from app.services.embedding_service import (
    EmbeddingService,
    claim_embedding_text,
    serialize_embedding,
)
from app.services.orchestrator import CanonOrchestrator

BASE = "https://helm.sh/docs"

CLAIMS = [
    # ============================================================
    # helm-errors — 30 common errors
    # ============================================================
    (
        "helm-errors", "helm error: INSTALLATION FAILED: cannot re-use a name that is still in use",
        "A release with that name already exists in the namespace. "
        "Fix: (1) pick a different name: `helm install <new-name> <chart>`; "
        "(2) upgrade existing instead: `helm upgrade <name> <chart>` (or `helm upgrade --install <name> <chart>` — install if absent, upgrade if present); "
        "(3) different namespace: `helm install <name> <chart> -n other-ns`; "
        "(4) confirm what's there: `helm list -A` shows releases across all namespaces; "
        "(5) clean up failed installs first: `helm uninstall <name>`.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-errors", "helm error: UPGRADE FAILED: \"<name>\" has no deployed releases",
        "Previous install left no successful revision (failed install). "
        "Fix: (1) check history: `helm history <name>` — all revisions probably 'failed'; "
        "(2) uninstall + reinstall: `helm uninstall <name> && helm install <name> <chart>`; "
        "(3) use `--install` flag on upgrade: `helm upgrade --install <name> <chart>` (treats no-deployed as fresh install); "
        "(4) Helm 3.14+: pass `--force` to force upgrade even without deployed releases.",
        f"{BASE}/helm/helm_upgrade/",
    ),
    (
        "helm-errors", "helm error: Kubernetes cluster unreachable / query: failed to query with labels",
        "Helm can't talk to your cluster — kubeconfig is wrong, cluster is down, or you're not authed. "
        "Fix: (1) verify with kubectl first: `kubectl get nodes`; "
        "(2) check kubeconfig: `kubectl config current-context`; "
        "(3) for cloud-managed (EKS/GKE/AKS): refresh creds (`aws eks update-kubeconfig`, `gcloud container clusters get-credentials`, `az aks get-credentials`); "
        "(4) explicit kubeconfig: `helm --kubeconfig /path/to/config list`; "
        "(5) env: `export KUBECONFIG=/path/to/config`.",
        f"{BASE}/helm/helm/",
    ),
    (
        "helm-errors", "helm error: failed to download \"<chart>\" / chart not found in repo",
        "Chart name not in repo, or you forgot to update repo index. "
        "Fix: (1) update local cache: `helm repo update`; "
        "(2) search: `helm search repo <name>` (only in repos you added); "
        "(3) hub-wide: `helm search hub <name>` (searches Artifact Hub); "
        "(4) repo prefix required: `helm install my-app bitnami/nginx` (not just `nginx`); "
        "(5) for OCI: `helm install my-app oci://registry.example.com/charts/nginx --version 1.0.0`.",
        f"{BASE}/helm/helm_search_repo/",
    ),
    (
        "helm-errors", "helm error: Looks like \"<repo>\" is not a valid chart repository / 404 / 502",
        "Repo URL is wrong, server's down, or the URL points to a website not a chart repo. "
        "Fix: (1) verify the index.yaml exists: `curl <repo-url>/index.yaml` should return YAML; "
        "(2) re-check the official URL — many charts moved off `charts.helm.sh/stable` (deprecated 2020); "
        "(3) for trusted alternative: `helm repo add bitnami https://charts.bitnami.com/bitnami`; "
        "(4) corporate proxy: `HTTPS_PROXY=...` before helm; "
        "(5) check status with `helm repo list`.",
        f"{BASE}/helm/helm_repo_add/",
    ),
    (
        "helm-errors", "helm error: chart requires kubeVersion: <X> which is incompatible with Kubernetes <Y>",
        "Chart's Chart.yaml specifies kubeVersion constraint your cluster doesn't satisfy. "
        "Fix: (1) check cluster version: `kubectl version --short`; "
        "(2) upgrade cluster, OR pick an older chart version: `helm install my-app chart --version 1.2.3`; "
        "(3) workaround (NOT recommended): `helm install --skip-tests` doesn't skip kubeVersion — you have to fork the chart and remove `kubeVersion` from Chart.yaml; "
        "(4) inspect chart's constraint: `helm show chart <chart>`.",
        f"{BASE}/topics/charts/",
    ),
    (
        "helm-errors", "helm error: parse error in <template>: <syntax>",
        "Go template syntax error in a chart's template/*.yaml. "
        "Fix: (1) render locally to see the file + line: `helm template <chart> --debug`; "
        "(2) common issues: missing `{{`/`}}`, wrong quote escaping, mismatched `{{ if }}` `{{ end }}`; "
        "(3) for chart you're authoring: `helm lint .` catches many of these; "
        "(4) for whitespace issues: use `{{- ... -}}` to trim, but be careful — over-trimming breaks YAML indentation.",
        f"{BASE}/chart_template_guide/functions_and_pipelines/",
    ),
    (
        "helm-errors", "helm error: nil pointer evaluating interface {}.X",
        "Template referenced a value that's nil (`.Values.something.missing.field`). "
        "Fix: (1) defensive access with `default`: `{{ .Values.image.tag | default \"latest\" }}`; "
        "(2) check before use: `{{- if .Values.persistence }}...{{ end }}`; "
        "(3) for deep paths: chain default — `{{ (.Values.image | default dict).tag | default \"latest\" }}`; "
        "(4) Helm 3.x: use `dig` to walk safely: `{{ dig \"image\" \"tag\" \"latest\" .Values }}`.",
        f"{BASE}/chart_template_guide/functions_and_pipelines/",
    ),
    (
        "helm-errors", "helm error: function \"X\" not defined",
        "Template called a function that doesn't exist — typo or non-stdlib function. "
        "Fix: (1) Helm functions: https://helm.sh/docs/chart_template_guide/function_list/ (full list); "
        "(2) common typos: `toYaml` (camelCase, NOT `toyaml`), `nindent` (NOT `indent` for new lines), `quote` (NOT `quotes`); "
        "(3) Sprig (extra functions Helm includes): http://masterminds.github.io/sprig/; "
        "(4) custom helpers go in `templates/_helpers.tpl` (filename must start with _ to skip rendering).",
        f"{BASE}/chart_template_guide/functions_and_pipelines/",
    ),
    (
        "helm-errors", "helm error: rendered manifests contain a resource that already exists",
        "Helm is trying to manage a resource that exists outside the release (created by kubectl, another release, or a previous failed install). "
        "Fix: (1) take ownership: `kubectl annotate <kind> <name> 'meta.helm.sh/release-name=<release>' 'meta.helm.sh/release-namespace=<ns>'` and `kubectl label <kind> <name> 'app.kubernetes.io/managed-by=Helm'`; "
        "(2) delete the orphan first: `kubectl delete <kind> <name>`; "
        "(3) for genuinely shared resources: exclude from chart, manage manually.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-errors", "helm error: required value not set / values failed schema validation",
        "Chart has `values.schema.json` enforcing required values, or template uses `required \"msg\" .Values.X`. "
        "Fix: (1) read the error — it tells you which value is missing; "
        "(2) provide via `--set X=value` or in `-f values.yaml`; "
        "(3) inspect required: `helm show values <chart>` (lists defaults + comments), then add to your overrides file; "
        "(4) check schema: `cat <chart-dir>/values.schema.json`.",
        f"{BASE}/topics/charts/",
    ),
    (
        "helm-errors", "helm error: forbidden: User \"X\" cannot <verb> resource <kind>",
        "Your kube user doesn't have RBAC to manage the resources your chart creates. "
        "Fix: (1) check perms: `kubectl auth can-i create deployments -n <ns>`; "
        "(2) get cluster-admin (only if appropriate): your admin grants `cluster-admin` ClusterRoleBinding; "
        "(3) for narrower perms: ask admin for `edit` role in target namespace; "
        "(4) for CRDs (often need cluster perms): some charts can install CRDs separately — use `--skip-crds` if they're pre-installed; "
        "(5) for service-account-installed helm (CI): grant SA `helm-install` role.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-errors", "helm error: failed to authenticate against registry / OCI 401",
        "Pulling from OCI registry without creds, or token expired. "
        "Fix: (1) `helm registry login registry.example.com` (interactive); "
        "(2) for GHCR: `echo $GITHUB_TOKEN | helm registry login ghcr.io -u <user> --password-stdin`; "
        "(3) for ECR: `aws ecr get-login-password | helm registry login --username AWS --password-stdin <id>.dkr.ecr.<region>.amazonaws.com`; "
        "(4) GAR: `gcloud auth print-access-token | helm registry login -u oauth2accesstoken --password-stdin <region>-docker.pkg.dev`; "
        "(5) verify with `helm registry logout` + re-login.",
        f"{BASE}/topics/registries/",
    ),
    (
        "helm-errors", "helm error: timed out waiting for the condition (--wait / hooks)",
        "Helm passed manifests to Kubernetes but waited too long for ready state. "
        "Fix: (1) extend timeout: `helm install --wait --timeout 10m my-app chart`; "
        "(2) check actual pod state: `kubectl get pods -n <ns>` — look for CrashLoopBackOff / ImagePullBackOff; "
        "(3) describe failing pod: `kubectl describe pod <pod>`; "
        "(4) hook timeout: `helm install --wait-for-jobs` waits for hook Jobs to complete; "
        "(5) skip wait + investigate manually: omit `--wait`.",
        f"{BASE}/topics/charts_hooks/",
    ),
    (
        "helm-errors", "helm error: invalid release name (DNS-1123)",
        "Release name must match RFC 1123 DNS label: lowercase, alphanumeric + hyphens, ≤53 chars, starts with letter. "
        "Fix: (1) rename: `my-app` ✓, `My_App` ✗, `123app` ✗; "
        "(2) for templated charts that build resource names from release name: keep short to avoid label-length cascades; "
        "(3) for generated names: `helm install --generate-name <chart>` lets Helm pick a valid one.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-errors", "helm error: unable to build kubernetes objects from release manifest / no matches for kind X",
        "Chart references a CRD/resource type the cluster doesn't have. "
        "Fix: (1) install CRDs first: many charts ship CRDs in `crds/` — Helm 3 installs them automatically on first install (NOT on upgrade); "
        "(2) for Helm 2-style CRDs (under `templates/`): order with hooks: `helm.sh/hook: crd-install`; "
        "(3) workaround for CRD install: `kubectl apply -f crds/`; "
        "(4) for upgrades: must manually update CRDs (Helm won't); "
        "(5) check: `kubectl api-resources | grep <kind>`.",
        f"{BASE}/topics/charts/",
    ),
    (
        "helm-errors", "helm error: chart's apiVersion 'v1' doesn't support 'dependencies'",
        "You added a `dependencies:` block to a chart with `apiVersion: v1` (Helm 2 era). "
        "Fix: (1) update Chart.yaml: `apiVersion: v2`; "
        "(2) Helm 2 used requirements.yaml — modern (v2) chart uses Chart.yaml dependencies section; "
        "(3) move requirements: `dependencies:\\n  - name: redis\\n    version: 17.x.x\\n    repository: https://charts.bitnami.com/bitnami`.",
        f"{BASE}/topics/charts/",
    ),
    (
        "helm-errors", "helm error: failed to parse --set data",
        "Wrong `--set` syntax. "
        "Fix: (1) basic: `--set key=value`; "
        "(2) nested: `--set service.type=NodePort`; "
        "(3) array: `--set tags={web,frontend}` (curly braces); "
        "(4) commas inside values: escape with backslash `--set message=hi\\,world`; "
        "(5) for complex: use `-f values.yaml` instead — much less error-prone; "
        "(6) string vs number: force string with `--set-string version=1` (otherwise becomes int).",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-errors", "helm error: dependency update failed",
        "`helm dep update` couldn't fetch a chart dependency. "
        "Fix: (1) verify deps in Chart.yaml are reachable: `curl <repo>/index.yaml`; "
        "(2) repo must be added: `helm repo add <name> <url>` BEFORE `helm dep update`; "
        "(3) check Chart.lock for stale entries — delete + retry `helm dep update`; "
        "(4) `helm dep build` uses existing Chart.lock without network; "
        "(5) for offline: `helm dep update --skip-refresh`.",
        f"{BASE}/helm/helm_dependency_update/",
    ),
    (
        "helm-errors", "helm error: secrets is forbidden (Helm 3 needs secrets in release namespace)",
        "Helm 3 stores release state in Secrets — service account needs `secrets` perms in the release namespace. "
        "Fix: (1) grant: ClusterRole/Role with `verbs: [get, list, watch, create, update, patch, delete]` on `secrets`; "
        "(2) bind to service account; "
        "(3) for cluster-admin: not needed but works; "
        "(4) alternative: use a different storage backend — `--driver configmap` (older default) or `--driver sql` (custom DB).",
        f"{BASE}/topics/advanced/",
    ),
    (
        "helm-errors", "helm error: Helm 2 (Tiller) commands no longer work in Helm 3",
        "`helm init`, `helm reset`, `helm tiller` etc removed in Helm 3 (no more Tiller server). "
        "Fix: (1) upgrade: `brew upgrade helm` or download Helm 3 binary; "
        "(2) migrate state: `helm plugin install https://github.com/helm/helm-2to3 && helm 2to3 convert <release>`; "
        "(3) drop `--tiller-namespace` flags; "
        "(4) Helm 3 stores releases as Secrets per-namespace (not in tiller-deploy).",
        f"{BASE}/topics/v2_v3_migration/",
    ),
    (
        "helm-errors", "helm error: at least one container in the pod must have an image specified",
        "Generated Deployment spec is missing `image:` for a container — usually a template bug or unset value. "
        "Fix: (1) check values: `helm get values <release>` — `image.repository` and `.tag` set?; "
        "(2) render to inspect: `helm template <chart> -f values.yaml | grep image`; "
        "(3) provide image: `helm install --set image.repository=nginx --set image.tag=1.25 ...`; "
        "(4) default in values.yaml: chart should ship sane defaults — file a bug if missing.",
        f"{BASE}/helm/helm_template/",
    ),
    (
        "helm-errors", "helm error: index.yaml is missing / 404 on index.yaml",
        "Repo URL is wrong, or it's a chart directory (not a packaged repo). "
        "Fix: (1) confirm: `curl <repo-url>/index.yaml` should return YAML; "
        "(2) for git-based: needs `helm package` + `helm repo index .` to be served as static files; "
        "(3) alternative: OCI registry doesn't need index.yaml — push with `helm push <chart>.tgz oci://...`; "
        "(4) self-hosted: serve over Pages, S3, GCS, or ChartMuseum.",
        f"{BASE}/topics/chart_repository/",
    ),
    (
        "helm-errors", "helm error: failed to upgrade: cannot patch X with kind Y (immutable field)",
        "Tried to change a Kubernetes-immutable field (e.g. Service.spec.clusterIP, Deployment.spec.selector). "
        "Fix: (1) for selector changes: must delete + recreate the Deployment (will cause downtime); "
        "(2) clusterIP: omit the field (let K8s assign) or accept the current value; "
        "(3) PVC storageClass: requires PVC delete (data loss risk); "
        "(4) safest path: uninstall + reinstall, OR use `helm upgrade --force` (recreates resources, downtime risk).",
        f"{BASE}/helm/helm_upgrade/",
    ),
    (
        "helm-errors", "helm error: chart \"X\" version constraint mismatch",
        "Subchart's version doesn't satisfy parent's `dependencies[*].version` semver range. "
        "Fix: (1) loosen range in Chart.yaml: `version: ^1.0.0` (allows 1.x) vs `1.2.3` (exact); "
        "(2) update parent's allowed range; "
        "(3) `helm dep update` re-resolves; "
        "(4) for invalid semvers (e.g. `v1.2`): switch to standard `1.2.0` format.",
        f"{BASE}/helm/helm_dependency_update/",
    ),
    (
        "helm-errors", "helm error: lookup function in template requires cluster connection",
        "`{{ lookup ... }}` queries the live cluster — not available during `helm template` (rendering offline). "
        "Fix: (1) use `helm install --dry-run` instead of `helm template` if you need cluster lookups; "
        "(2) for tests: pass values directly instead of looking up; "
        "(3) check existence: `{{- if (lookup \"v1\" \"Secret\" .Release.Namespace \"mysec\") }}` guards; "
        "(4) alternative: provide the value via `--set` rather than discovering from cluster.",
        f"{BASE}/chart_template_guide/functions_and_pipelines/",
    ),
    (
        "helm-errors", "helm error: rollback failed: release has no revisions",
        "You tried `helm rollback X` but only one revision exists (or all are failed). "
        "Fix: (1) check: `helm history <release>` — needs ≥ 2 deployed revisions to rollback; "
        "(2) for fresh installs: nothing to rollback to — `helm uninstall` + reinstall; "
        "(3) rollback to specific revision: `helm rollback <release> 3` (revision 3); "
        "(4) `--cleanup-on-fail` flag during install/upgrade auto-rollbacks on failure.",
        f"{BASE}/helm/helm_rollback/",
    ),
    (
        "helm-errors", "helm error: cannot use template (X) with arguments",
        "Called a named template wrong — `include` vs `template` distinction. "
        "Fix: (1) use `include`: `{{ include \"chart.fullname\" . }}` (returns string, pipeable); "
        "(2) NOT `template`: that's an action, not pipeable (can't use `| indent` etc); "
        "(3) defined templates live in `_helpers.tpl`: `{{- define \"chart.fullname\" -}}{{- ... -}}{{- end }}`; "
        "(4) common: `{{ include \"chart.labels\" . | nindent 4 }}` for proper indentation.",
        f"{BASE}/chart_template_guide/named_templates/",
    ),
    (
        "helm-errors", "helm error: invalid value: '' for path: chart hooks must have a hook annotation",
        "Hook resource (Job/Pod) is missing the `helm.sh/hook` annotation. "
        "Fix: (1) annotate: ```yaml\\nmetadata:\\n  annotations:\\n    'helm.sh/hook': pre-install,pre-upgrade\\n    'helm.sh/hook-weight': '-5'\\n    'helm.sh/hook-delete-policy': hook-succeeded\\n```; "
        "(2) common hooks: `pre-install`, `post-install`, `pre-upgrade`, `post-upgrade`, `pre-delete`, `post-delete`, `pre-rollback`, `post-rollback`, `test`; "
        "(3) delete policies: `before-hook-creation` (default), `hook-succeeded`, `hook-failed`.",
        f"{BASE}/topics/charts_hooks/",
    ),
    (
        "helm-errors", "helm error: --reuse-values + --set conflict / values being reset",
        "On upgrade, `--reuse-values` re-uses last values but ignores new -f / --set, while `--reset-values` resets to defaults. Mixing causes surprises. "
        "Fix: (1) for safe upgrade preserving overrides: `helm upgrade --reuse-values <release> <chart>` (uses last values); "
        "(2) to update one value: `--reuse-values --set image.tag=2.0`; "
        "(3) to start fresh: `--reset-values -f new-values.yaml`; "
        "(4) default (neither): merges new -f/--set ON TOP of last values (similar to --reuse-values + overrides).",
        f"{BASE}/helm/helm_upgrade/",
    ),

    # ============================================================
    # helm-recipes — 36 common workflows
    # ============================================================
    (
        "helm-recipes", "helm recipe: install Helm itself",
        "macOS: `brew install helm`. "
        "Linux: `curl https://baltocdn.com/helm/signing.asc | sudo apt-key add - && echo \"deb https://baltocdn.com/helm/stable/debian/ all main\" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list && sudo apt update && sudo apt install helm`. "
        "Universal script: `curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash`. "
        "Verify: `helm version` (Helm 3.x — no Tiller needed).",
        f"{BASE}/intro/install/",
    ),
    (
        "helm-recipes", "helm recipe: add a chart repository",
        "`helm repo add bitnami https://charts.bitnami.com/bitnami` (Bitnami is a popular general-purpose repo). "
        "Other common: `prometheus-community https://prometheus-community.github.io/helm-charts`; `ingress-nginx https://kubernetes.github.io/ingress-nginx`; `jetstack https://charts.jetstack.io` (cert-manager). "
        "After adding, ALWAYS run `helm repo update` to fetch the latest index.yaml. "
        "List your repos: `helm repo list`.",
        f"{BASE}/helm/helm_repo_add/",
    ),
    (
        "helm-recipes", "helm recipe: update repo indexes before installing",
        "`helm repo update` fetches latest index.yaml from all configured repos. "
        "Specific repo: `helm repo update bitnami`. "
        "Always run before searching for the newest versions: `helm repo update && helm search repo nginx`. "
        "After update: `helm search repo <name> --versions` shows all available versions.",
        f"{BASE}/helm/helm_repo_update/",
    ),
    (
        "helm-recipes", "helm recipe: search for a chart",
        "Local repos: `helm search repo nginx`. "
        "All versions: `helm search repo nginx --versions`. "
        "Public hub (Artifact Hub): `helm search hub postgresql`. "
        "Inspect before installing: `helm show chart bitnami/nginx` (Chart.yaml content); `helm show values bitnami/nginx` (default values); `helm show readme bitnami/nginx`.",
        f"{BASE}/helm/helm_search_repo/",
    ),
    (
        "helm-recipes", "helm recipe: install with default values",
        "`helm install my-release bitnami/nginx`. "
        "Specific version: `--version 15.0.0`. "
        "Namespace (must exist): `-n production`. "
        "Auto-create namespace: `-n production --create-namespace`. "
        "After: `helm status my-release -n production` shows state + connection details.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-recipes", "helm recipe: install with a custom values file",
        "Create `my-values.yaml` with overrides only. "
        "`helm install my-release bitnami/nginx -f my-values.yaml`. "
        "Multiple files (later wins on conflicts): `-f base.yaml -f prod.yaml`. "
        "View defaults to copy: `helm show values bitnami/nginx > defaults.yaml`. "
        "Best practice: keep ONLY overrides in your file (not full copy of defaults).",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-recipes", "helm recipe: override values inline with --set",
        "Single value: `--set replicaCount=3`. "
        "Nested: `--set service.type=LoadBalancer`. "
        "Multiple: `--set replicaCount=3,service.type=NodePort`. "
        "Arrays: `--set tags={a,b,c}`. "
        "Force string: `--set-string image.tag='1.25.3'` (prevents int coercion). "
        "From file: `--set-file ssh_key=./key.pub` (loads file contents as value).",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-recipes", "helm recipe: install-or-upgrade idempotently (--install flag)",
        "`helm upgrade --install my-release bitnami/nginx -f values.yaml`. "
        "Creates the release if it doesn't exist, upgrades if it does — perfect for CI/CD. "
        "Add `--atomic` for auto-rollback on failure. "
        "Add `--wait` to block until all resources are Ready. "
        "Add `--timeout 10m` to extend the default 5min wait.",
        f"{BASE}/helm/helm_upgrade/",
    ),
    (
        "helm-recipes", "helm recipe: upgrade a release",
        "Same chart, new values: `helm upgrade my-release bitnami/nginx -f new-values.yaml`. "
        "Bump chart version: `helm upgrade my-release bitnami/nginx --version 15.1.0`. "
        "Reuse last values + tweak one: `helm upgrade my-release chart --reuse-values --set image.tag=2.0`. "
        "Safe upgrade (auto-rollback if any pod fails to become Ready): `--atomic --timeout 10m`. "
        "Dry run: `--dry-run` shows what would change.",
        f"{BASE}/helm/helm_upgrade/",
    ),
    (
        "helm-recipes", "helm recipe: rollback to a previous revision",
        "Last good: `helm rollback my-release` (rolls back one revision). "
        "Specific revision: `helm rollback my-release 3`. "
        "List revisions: `helm history my-release` shows all with status. "
        "Atomic upgrades (auto-rollback): `helm upgrade --atomic ...` — recommended for prod. "
        "Note: rollback creates a NEW revision in history (e.g. revision 5 'rollback of 3').",
        f"{BASE}/helm/helm_rollback/",
    ),
    (
        "helm-recipes", "helm recipe: uninstall a release",
        "`helm uninstall my-release` (Helm 3 — no `--purge` needed). "
        "Specific namespace: `-n production`. "
        "Keep history: `--keep-history` (allows future rollback). "
        "Dry run: `--dry-run`. "
        "Helm-managed resources removed, but PVCs (persistent storage) often remain by design — `kubectl delete pvc -l app=my-app` if you want them gone.",
        f"{BASE}/helm/helm_uninstall/",
    ),
    (
        "helm-recipes", "helm recipe: list releases",
        "Current namespace: `helm list`. "
        "All namespaces: `helm list -A`. "
        "Filter by status: `helm list --failed` / `--pending` / `--deployed`. "
        "Filter by name pattern: `helm list -f '^my'` (regex). "
        "Output format: `helm list -o json` (or yaml/table).",
        f"{BASE}/helm/helm_list/",
    ),
    (
        "helm-recipes", "helm recipe: render templates locally (no install)",
        "`helm template my-release ./chart -f values.yaml` prints all rendered Kubernetes manifests. "
        "Skip helper templates: `--skip-tests` excludes test resources. "
        "Specific template only: `--show-only templates/deployment.yaml`. "
        "Use case: GitOps preview, debugging template logic, kubectl-apply workflow. "
        "Caveat: `lookup` functions don't work (no cluster connection) — use `helm install --dry-run` for that.",
        f"{BASE}/helm/helm_template/",
    ),
    (
        "helm-recipes", "helm recipe: dry-run before installing",
        "`helm install my-release ./chart --dry-run --debug -f values.yaml`. "
        "Shows what would be created, validates against cluster (catches RBAC + schema issues unlike `helm template`). "
        "For full server-side validation: `--dry-run=server`. "
        "Render-only without cluster check: use `helm template` instead.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-recipes", "helm recipe: diff before upgrading (helm-diff plugin)",
        "Install plugin: `helm plugin install https://github.com/databus23/helm-diff`. "
        "Then: `helm diff upgrade my-release ./chart -f new-values.yaml`. "
        "Shows red/green diff of what would change in the cluster. "
        "Essential for production upgrades — gives confidence before pulling the trigger. "
        "Also: `helm diff release <name>` shows drift between manifest and live state.",
        f"{BASE}/helm/helm_plugin/",
    ),
    (
        "helm-recipes", "helm recipe: get current values / manifest of a release",
        "Values you set: `helm get values my-release`. "
        "All values (including defaults): `helm get values my-release --all`. "
        "Rendered manifest: `helm get manifest my-release`. "
        "NOTES.txt: `helm get notes my-release`. "
        "Hooks: `helm get hooks my-release`. "
        "Specific revision: add `--revision N`.",
        f"{BASE}/helm/helm_get_values/",
    ),
    (
        "helm-recipes", "helm recipe: create a new chart skeleton",
        "`helm create my-app` generates a working starter chart in `my-app/`. "
        "Structure: `Chart.yaml`, `values.yaml`, `templates/{deployment,service,ingress,hpa,serviceaccount}.yaml`, `templates/_helpers.tpl`, `templates/tests/test-connection.yaml`. "
        "Replace generated stuff with your app. "
        "Lint as you go: `helm lint my-app`. "
        "Test render: `helm template my-app ./my-app`.",
        f"{BASE}/helm/helm_create/",
    ),
    (
        "helm-recipes", "helm recipe: lint a chart",
        "`helm lint ./chart` checks for chart issues + missing required fields + values schema violations. "
        "Strict mode (warnings = errors): `--strict`. "
        "Lint multiple charts: `helm lint ./charts/*`. "
        "With overridden values: `helm lint ./chart -f values-prod.yaml`. "
        "Returns exit 0 on success — perfect for CI: `helm lint ./chart || exit 1`.",
        f"{BASE}/helm/helm_lint/",
    ),
    (
        "helm-recipes", "helm recipe: declare and update chart dependencies",
        "In Chart.yaml: ```yaml\\ndependencies:\\n  - name: postgresql\\n    version: 15.x.x\\n    repository: https://charts.bitnami.com/bitnami\\n    condition: postgresql.enabled\\n```. "
        "First-time: `helm dep update` (fetches into charts/ + writes Chart.lock). "
        "Reproducible: `helm dep build` (uses Chart.lock, doesn't refresh from network — for CI). "
        "Conditional: `condition: <flag>` + `tags: [list]` toggle subcharts in/out via values.",
        f"{BASE}/helm/helm_dependency_update/",
    ),
    (
        "helm-recipes", "helm recipe: package a chart for distribution",
        "`helm package ./my-app` produces `my-app-1.0.0.tgz` (uses Chart.yaml version). "
        "Sign with GPG: `helm package --sign --key 'my-key' --keyring ~/.gnupg/secring.gpg ./my-app` (produces .tgz + .prov). "
        "Push to OCI registry: `helm push my-app-1.0.0.tgz oci://registry.example.com/charts`. "
        "Update an HTTP repo: `helm repo index ./repo-dir --url https://charts.example.com` (regenerates index.yaml).",
        f"{BASE}/helm/helm_package/",
    ),
    (
        "helm-recipes", "helm recipe: push and pull from OCI registry",
        "Auth first: `helm registry login ghcr.io -u <user> --password-stdin <<< $TOKEN`. "
        "Push: `helm push my-chart-1.0.0.tgz oci://ghcr.io/myorg/charts`. "
        "Pull: `helm pull oci://ghcr.io/myorg/charts/my-chart --version 1.0.0`. "
        "Install directly: `helm install my-app oci://ghcr.io/myorg/charts/my-chart --version 1.0.0`. "
        "Major registries supporting OCI helm: GHCR, ECR, GAR, ACR, Harbor.",
        f"{BASE}/topics/registries/",
    ),
    (
        "helm-recipes", "helm recipe: write multi-environment values files",
        "Convention: `values.yaml` (defaults), `values-staging.yaml`, `values-prod.yaml`. "
        "Install for env: `helm install -f values.yaml -f values-prod.yaml my-release ./chart`. "
        "Later file overrides earlier. "
        "Per-env keys (image tags, replicas, ingress hosts, secrets via external-secrets) live in env-specific files. "
        "Common values stay in base.",
        f"{BASE}/chart_template_guide/values_files/",
    ),
    (
        "helm-recipes", "helm recipe: use values from CI/CD pipeline",
        "Pass values dynamically: `helm upgrade --install my-release ./chart --set image.tag=$CI_COMMIT_SHA --set environment=$ENV -f values.yaml`. "
        "From env var → values file (e.g. envsubst): `envsubst < values.yaml.tmpl > values.yaml && helm upgrade ...`. "
        "From CI vars: `--set replicas=$REPLICAS`. "
        "For complex CI: generate yaml in pipeline + commit to ArgoCD/Flux repo.",
        f"{BASE}/helm/helm_install/",
    ),
    (
        "helm-recipes", "helm recipe: test a chart (helm test post-install)",
        "After install: `helm test my-release` runs test pods/jobs annotated with `helm.sh/hook: test`. "
        "Templates live in `templates/tests/`. "
        "Common test: BusyBox pod that `wget`s your service to verify connectivity. "
        "Example: ```yaml\\napiVersion: v1\\nkind: Pod\\nmetadata:\\n  name: test-connection\\n  annotations: 'helm.sh/hook': test\\nspec:\\n  containers:\\n    - name: wget\\n      image: busybox\\n      command: ['wget', 'http://my-svc:8080']\\n  restartPolicy: Never\\n```.",
        f"{BASE}/helm/helm_test/",
    ),
    (
        "helm-recipes", "helm recipe: use hooks (pre-install / post-upgrade / pre-delete)",
        "Add to resource's annotations: ```yaml\\nmetadata:\\n  annotations:\\n    'helm.sh/hook': pre-install,pre-upgrade\\n    'helm.sh/hook-weight': '-5'\\n    'helm.sh/hook-delete-policy': hook-succeeded\\n```. "
        "Use cases: DB migrations (Job), backup snapshots, config validation. "
        "Hooks fire in weight order (negative first). "
        "Delete policies: `before-hook-creation` (default), `hook-succeeded` (clean up), `hook-failed`, `never`.",
        f"{BASE}/topics/charts_hooks/",
    ),
    (
        "helm-recipes", "helm recipe: validate values.yaml with JSON Schema",
        "Add `values.schema.json` next to values.yaml. "
        "Example: ```json\\n{\\n  \"$schema\": \"https://json-schema.org/draft-07/schema#\",\\n  \"required\": [\"image\"],\\n  \"properties\": {\\n    \"image\": {\\n      \"type\": \"object\",\\n      \"required\": [\"repository\", \"tag\"],\\n      \"properties\": {\\n        \"repository\": {\"type\": \"string\"},\\n        \"tag\": {\"type\": \"string\"}\\n      }\\n    }\\n  }\\n}\\n```. "
        "Enforced on install/upgrade. Auto-generated tools: `helm-schema` plugin.",
        f"{BASE}/topics/charts/",
    ),
    (
        "helm-recipes", "helm recipe: define + use named templates (helpers)",
        "In `templates/_helpers.tpl`: ```\\n{{- define \"mychart.labels\" -}}\\napp: {{ .Release.Name }}\\nchart: {{ .Chart.Name }}-{{ .Chart.Version }}\\n{{- end }}\\n```. "
        "Use in templates: `{{ include \"mychart.labels\" . | nindent 4 }}` (indents for proper YAML). "
        "Leading underscore in filename means Helm doesn't try to render as a Kubernetes object. "
        "Always pass `.` (context) to access values.",
        f"{BASE}/chart_template_guide/named_templates/",
    ),
    (
        "helm-recipes", "helm recipe: embed config files via .Files",
        "Put files in `chart/files/` (NOT templates/). "
        "Reference: ```yaml\\napiVersion: v1\\nkind: ConfigMap\\ndata:\\n  config.yaml: |-\\n    {{ .Files.Get \"files/config.yaml\" | nindent 4 }}\\n```. "
        "Glob multiple files: `{{ range $path, $bytes := .Files.Glob \"files/*.conf\" }}\\n{{ base $path }}: |\\n{{ $bytes | nindent 2 }}\\n{{ end }}`. "
        "Useful for shipping default app config inside the chart.",
        f"{BASE}/chart_template_guide/accessing_files/",
    ),
    (
        "helm-recipes", "helm recipe: make resources name-unique with Release.Name",
        "Avoid collisions when installing the same chart multiple times in same namespace. "
        "Template: `{{ .Release.Name }}-deployment` (e.g. `my-release-deployment`). "
        "Common helper: ```\\n{{- define \"mychart.fullname\" -}}\\n{{- $name := default .Chart.Name .Values.nameOverride -}}\\n{{- printf \"%s-%s\" .Release.Name $name | trunc 63 | trimSuffix \"-\" -}}\\n{{- end }}\\n```. "
        "`trunc 63` enforces K8s 63-char DNS label limit.",
        f"{BASE}/chart_template_guide/builtin_objects/",
    ),
    (
        "helm-recipes", "helm recipe: conditionally include resources",
        "Inside template: `{{- if .Values.ingress.enabled }}` ... `{{- end }}`. "
        "Whole template won't render if false. "
        "Combine: `{{- if and .Values.ingress.enabled .Values.tls }}` ... "
        "Negation: `{{- if not .Values.legacy }}` ... "
        "Subcharts via `condition: <flag>` in Chart.yaml dependencies — enables/disables whole subchart.",
        f"{BASE}/chart_template_guide/control_structures/",
    ),
    (
        "helm-recipes", "helm recipe: debug template rendering",
        "`helm install --debug --dry-run my-release ./chart -f values.yaml`. "
        "Shows what would be created + any template errors with line numbers. "
        "Print values for inspection: in template `{{- if .Values.debug }}{{ .Values | toYaml | nindent 2 }}{{- end }}`. "
        "Render to file for inspection: `helm template ./chart > rendered.yaml`. "
        "Compare versions: `helm template ./chart -f old.yaml > old.yaml && helm template ./chart -f new.yaml > new.yaml && diff old.yaml new.yaml`.",
        f"{BASE}/helm/helm_template/",
    ),
    (
        "helm-recipes", "helm recipe: sign + verify charts (provenance)",
        "Sign: `helm package --sign --key 'my-key' --keyring ~/.gnupg/secring.gpg ./chart` produces `chart-1.0.0.tgz` + `chart-1.0.0.tgz.prov`. "
        "Distribute both. "
        "Verify on install: `helm install --verify my-release chart-1.0.0.tgz` (uses sender's public key in your keyring). "
        "GPG version compat: Helm uses gpg with classic keyring — recent gpg may need `gpg --export-secret-keys > ~/.gnupg/secring.gpg` first.",
        f"{BASE}/topics/provenance/",
    ),
    (
        "helm-recipes", "helm recipe: migrate from Helm 2 → Helm 3",
        "Install helm-2to3 plugin: `helm plugin install https://github.com/helm/helm-2to3`. "
        "Migrate config (~/.helm → ~/.config/helm): `helm 2to3 move config`. "
        "Migrate one release: `helm 2to3 convert <release-name>` (idempotent). "
        "Bulk: `helm 2to3 convert --all`. "
        "Clean up Tiller after verifying migrations: `helm 2to3 cleanup --tiller-cleanup --release-cleanup`.",
        f"{BASE}/topics/v2_v3_migration/",
    ),
    (
        "helm-recipes", "helm recipe: organize labels per Kubernetes recommendations",
        "Use standard `app.kubernetes.io/*` labels. "
        "Helm-recommended in `_helpers.tpl`: ```\\n{{- define \"mychart.labels\" -}}\\nhelm.sh/chart: {{ include \"mychart.chart\" . }}\\napp.kubernetes.io/name: {{ include \"mychart.name\" . }}\\napp.kubernetes.io/instance: {{ .Release.Name }}\\napp.kubernetes.io/version: {{ .Chart.AppVersion | quote }}\\napp.kubernetes.io/managed-by: {{ .Release.Service }}\\n{{- end }}\\n```. "
        "Apply: `{{ include \"mychart.labels\" . | nindent 4 }}`.",
        f"{BASE}/chart_best_practices/labels/",
    ),
    (
        "helm-recipes", "helm recipe: use library charts (DRY)",
        "Library chart = collection of templates without renderable resources, intended for `include` from other charts. "
        "Set `type: library` in Chart.yaml. "
        "Used as dep: ```yaml\\ndependencies:\\n  - name: common\\n    version: 2.x\\n    repository: https://my-charts.example.com\\n```. "
        "Include in app chart: `{{ include \"common.deployment\" . }}`. "
        "Use case: ship standard Deployment / Service templates across multiple application charts.",
        f"{BASE}/topics/library_charts/",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (tool_id, subject, statement, evidence_url) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-helm-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-helm-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="helm-catalog",
            evidence=[evidence],
            risk_level=RiskLevel.LOW,
            created_at=now,
        )
        try:
            store.create(claim)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ #{suffix} create: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        try:
            verification = orchestrator.verify_claim(claim)
            store.save_verification_result(verification)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ #{suffix} verify: {type(exc).__name__}: {exc}")
        text = claim_embedding_text(subject=subject, statement=statement)
        vec = svc.embed_one(text)
        store.set_embedding(claim.claim_id, serialize_embedding(vec))
        ok += 1
        print(f"  ✓ #{suffix} [{tool_id}] {subject[:70]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()

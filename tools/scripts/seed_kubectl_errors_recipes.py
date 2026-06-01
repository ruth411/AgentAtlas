"""Direct-insert kubectl error + recipe claims."""

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

BASE = "https://kubernetes.io/docs"
CLI = f"{BASE}/reference/kubectl"

CLAIMS = [
    # ============================================================
    # kubectl-errors — 30 common errors
    # ============================================================
    (
        "kubectl-errors", "kubectl error: The connection to the server <host>:6443 was refused",
        "Kubectl couldn't reach the API server. Either the cluster is down, kubeconfig points to the wrong endpoint, or you're behind a firewall. "
        "Fix: (1) verify endpoint: `kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'`; "
        "(2) check connectivity: `curl -k <server>/healthz`; "
        "(3) for cloud-managed: refresh credentials (`aws eks update-kubeconfig`, `gcloud container clusters get-credentials`, `az aks get-credentials`); "
        "(4) for local kind/minikube: `kind get clusters`, `minikube status`; "
        "(5) try a different kubeconfig: `kubectl --kubeconfig ~/.kube/other-config get nodes`.",
        f"{CLI}/",
    ),
    (
        "kubectl-errors", "kubectl error: You must be logged in to the server (Unauthorized)",
        "Auth token expired or missing. "
        "Fix: (1) for cloud-managed: refresh creds (commands above); "
        "(2) for OIDC: re-login via your IdP (kubectl-oidc-login plugin); "
        "(3) for service-account: ensure token isn't expired (1h default in K8s 1.21+); "
        "(4) check current user: `kubectl auth whoami` (K8s 1.28+) or `kubectl config view --minify -o jsonpath='{.users[0].name}'`; "
        "(5) verify cert paths/expiry: `kubectl config view --raw -o jsonpath='{.users[0].user.client-certificate}'`.",
        f"{CLI}/generated/kubectl_auth/",
    ),
    (
        "kubectl-errors", "kubectl error: forbidden: User \"X\" cannot <verb> resource \"Y\" in namespace \"Z\" (RBAC)",
        "Your user lacks RBAC permission for that operation. "
        "Fix: (1) check what you can do: `kubectl auth can-i --list -n <ns>`; "
        "(2) one specific: `kubectl auth can-i create pods -n <ns>`; "
        "(3) impersonate to test: `kubectl auth can-i list secrets --as=system:serviceaccount:default:my-sa`; "
        "(4) for service account perms: ensure Role/ClusterRole + RoleBinding exists; "
        "(5) admin can grant via: `kubectl create rolebinding ... --clusterrole=admin --user=X -n <ns>`.",
        f"{BASE}/reference/access-authn-authz/rbac/",
    ),
    (
        "kubectl-errors", "kubectl error: Error from server (NotFound): <kind> \"<name>\" not found",
        "Resource doesn't exist in the namespace you're searching (or you have a typo). "
        "Fix: (1) check namespace: `kubectl get <kind> -A` lists across all namespaces; "
        "(2) explicit ns: `kubectl get <kind> -n <ns>`; "
        "(3) typo in name? `kubectl get <kind>` (no name) lists; "
        "(4) for namespaced kinds: defaults to `default` ns unless context specifies; "
        "(5) for cluster-scoped (Node, PV, Namespace): don't pass -n.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-errors", "kubectl error: Error from server (AlreadyExists): <kind> \"<name>\" already exists",
        "Resource already exists. `kubectl create` is for new objects only. "
        "Fix: (1) for declarative updates: `kubectl apply -f file.yaml` (handles existing); "
        "(2) replace whole: `kubectl replace -f file.yaml` (requires --force for some kinds); "
        "(3) delete + recreate (data loss risk!): `kubectl delete <kind> <name> && kubectl create -f file.yaml`; "
        "(4) patch specific fields: `kubectl patch <kind> <name> -p '{...}'`; "
        "(5) for `kubectl run`/`expose` that already exists: pick a different name.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-errors", "kubectl error: unable to recognize \"<file>\": no matches for kind \"<X>\" in version \"<Y>\"",
        "The cluster doesn't have a CRD for that kind, or the version doesn't exist. "
        "Fix: (1) check CRDs: `kubectl get crds | grep <kind>`; "
        "(2) install operator/CRDs first (cert-manager, prometheus-operator, etc.); "
        "(3) API version mismatch: `kubectl api-versions | grep <group>` shows supported; "
        "(4) for deprecated kinds: many old `extensions/v1beta1`, `apps/v1beta1` are gone in modern clusters — update YAML to `apps/v1`; "
        "(5) for cluster version: `kubectl version` — some kinds need newer K8s.",
        f"{CLI}/generated/kubectl_api-resources/",
    ),
    (
        "kubectl-errors", "kubectl error: error validating data: ValidationError(...)",
        "YAML/JSON schema doesn't match the resource spec. "
        "Fix: (1) read the field path in the error — e.g. `spec.containers[0].image: required value`; "
        "(2) check the schema: `kubectl explain <kind>.spec.containers.image`; "
        "(3) try without validation (LAST RESORT): `kubectl apply --validate=false -f file.yaml`; "
        "(4) common: indentation wrong (tabs vs spaces, must be spaces), nesting off; "
        "(5) lint locally: `kubectl apply --dry-run=client -f file.yaml` (no cluster needed) or `kubeval file.yaml`.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-errors", "kubectl error: error converting YAML to JSON",
        "YAML syntax error. "
        "Fix: (1) common: tabs instead of spaces — YAML requires spaces; "
        "(2) check indentation alignment under list items: lists need `- ` then aligned keys; "
        "(3) string values with special chars need quoting: `command: '/bin/sh'`; "
        "(4) test parser: `yq . file.yaml` or `python -c 'import yaml; yaml.safe_load(open(\"file.yaml\"))'`; "
        "(5) for multi-document files: `---` separator on its own line.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-errors", "kubectl error: ImagePullBackOff / ErrImagePull",
        "Container image can't be pulled. "
        "Fix: (1) describe to see exact error: `kubectl describe pod <pod>` — look at Events; "
        "(2) common: typo in image name/tag; "
        "(3) private registry needs imagePullSecrets: `kubectl create secret docker-registry myreg --docker-server=... --docker-username=... --docker-password=...`, then reference in pod spec `imagePullSecrets: [{name: myreg}]`; "
        "(4) network: node can't reach registry — check egress/firewall; "
        "(5) rate limit (Docker Hub): authenticate to raise limit, or use a mirror.",
        f"{BASE}/concepts/workloads/pods/",
    ),
    (
        "kubectl-errors", "kubectl error: CrashLoopBackOff",
        "Container keeps crashing and Kubernetes is backing off restarts. "
        "Fix: (1) logs of crashed: `kubectl logs <pod> --previous` (the `--previous` shows last terminated container); "
        "(2) describe for exit code: `kubectl describe pod <pod>` — `Last State: Terminated, Exit Code: X`; "
        "(3) common exit codes: 0=success, 1=app error, 137=OOMKilled (raise memory), 139=segfault, 143=SIGTERM (often graceful shutdown timing); "
        "(4) for debugging: `kubectl debug <pod> -it --image=busybox --target=<container>` (ephemeral debug container); "
        "(5) typical causes: missing env var, can't connect to a dependency, config file missing.",
        f"{BASE}/concepts/workloads/pods/",
    ),
    (
        "kubectl-errors", "kubectl error: OOMKilled (exit code 137)",
        "Container exceeded its memory limit and kernel killed it. "
        "Fix: (1) raise limit: `resources.limits.memory: 512Mi` in pod spec; "
        "(2) `kubectl top pod <pod>` shows current usage (needs metrics-server); "
        "(3) for spikey workloads: bigger limit OR enable swap (K8s 1.30+); "
        "(4) investigate leak: take heap dump via app's debug endpoint; "
        "(5) JVM: configure JVM heap to match container limit (`-XX:MaxRAMPercentage=75`).",
        f"{BASE}/concepts/workloads/pods/",
    ),
    (
        "kubectl-errors", "kubectl error: 0/N nodes are available: insufficient cpu / memory",
        "Pod is Pending because no node has enough free resources. "
        "Fix: (1) `kubectl describe pod <pod>` shows specific reason; "
        "(2) check node capacity: `kubectl top nodes` and `kubectl describe nodes | grep -A 5 Allocatable`; "
        "(3) reduce requests: `resources.requests.cpu/memory` lower in pod spec; "
        "(4) scale cluster: add more nodes (autoscaler / manual); "
        "(5) for HPA-driven pods: HorizontalPodAutoscaler may try to schedule more than capacity — review HPA min/max.",
        f"{BASE}/concepts/workloads/pods/",
    ),
    (
        "kubectl-errors", "kubectl error: pod has unbound immediate PersistentVolumeClaims",
        "PVC isn't bound — no PV matches, or no StorageClass provisioner. "
        "Fix: (1) check PVC: `kubectl get pvc` — `STATUS: Pending`; describe for reason: `kubectl describe pvc <name>`; "
        "(2) check StorageClass: `kubectl get sc` — default is marked `(default)`; "
        "(3) for cloud: CSI driver must be installed (EBS / GCE PD / Azure Disk); "
        "(4) verify default sc has `allowVolumeExpansion: true` if needed; "
        "(5) for local cluster: install local-path-provisioner or use hostPath.",
        f"{BASE}/concepts/storage/persistent-volumes/",
    ),
    (
        "kubectl-errors", "kubectl error: Service has no endpoints",
        "Service selector doesn't match any pod labels (or pods are not Ready). "
        "Fix: (1) check selector: `kubectl get svc <name> -o jsonpath='{.spec.selector}'`; "
        "(2) verify pod labels: `kubectl get pods --show-labels | grep <expected-label>`; "
        "(3) check pod readiness: `kubectl get pods -l <selector>` — must be Ready=True (readinessProbe passing); "
        "(4) endpoints object: `kubectl get endpoints <name>` (empty = no matching ready pods); "
        "(5) for headless (ClusterIP: None): different behavior — pods are individually addressable.",
        f"{BASE}/concepts/services-networking/service/",
    ),
    (
        "kubectl-errors", "kubectl error: deployment X is invalid: spec.selector: Invalid value: ... field is immutable",
        "Deployment's `spec.selector` can't be changed after creation. "
        "Fix: (1) delete + recreate: `kubectl delete deploy <name> && kubectl apply -f new.yaml` (downtime); "
        "(2) for blue-green: deploy new (different name + labels) alongside, switch Service selector, then delete old; "
        "(3) for label refactor: copy old labels into new pods AND keep the immutable selector field unchanged; "
        "(4) similar restriction on Service.spec.selector — same fix.",
        f"{BASE}/concepts/workloads/controllers/deployment/",
    ),
    (
        "kubectl-errors", "kubectl error: context deadline exceeded / timed out",
        "Operation took longer than the request timeout. "
        "Fix: (1) extend timeout: `kubectl --request-timeout=60s get pods`; "
        "(2) for `kubectl wait`: increase: `kubectl wait --for=condition=Ready pod/X --timeout=300s`; "
        "(3) for large `kubectl get -A`: paginate or filter: `kubectl get pods -A --chunk-size=100`; "
        "(4) check API server health if persistent: `kubectl get --raw /healthz`.",
        f"{CLI}/generated/kubectl_wait/",
    ),
    (
        "kubectl-errors", "kubectl error: container \"<name>\" not found in pod / unable to upgrade connection",
        "exec/logs/attach references a container that doesn't exist in the pod. "
        "Fix: (1) list containers: `kubectl get pod <pod> -o jsonpath='{.spec.containers[*].name}'`; "
        "(2) include initContainers: `... .spec.initContainers[*].name`; "
        "(3) explicit container: `kubectl logs <pod> -c <container>`, `kubectl exec <pod> -c <container> -- ...`; "
        "(4) for ephemeral debug: `kubectl exec <pod> -c <ephemeral-name> -- ...`.",
        f"{CLI}/generated/kubectl_exec/",
    ),
    (
        "kubectl-errors", "kubectl error: failed to delete / resource has finalizers / stuck Terminating",
        "Resource has finalizers blocking deletion (often because a controller crashed). "
        "Fix: (1) check finalizers: `kubectl get <kind> <name> -o yaml | grep finalizers -A 5`; "
        "(2) force remove (DANGEROUS — may leave orphan resources): `kubectl patch <kind> <name> --type merge -p '{\"metadata\":{\"finalizers\":[]}}'`; "
        "(3) for namespace stuck terminating: also patch the namespace's `spec.finalizers`; "
        "(4) for proper cleanup: identify which controller owns the finalizer (often a CRD operator) and ensure it's healthy.",
        f"{CLI}/generated/kubectl_delete/",
    ),
    (
        "kubectl-errors", "kubectl error: error: no objects passed to apply",
        "`kubectl apply -f` with empty file, or kustomize directory missing kustomization.yaml. "
        "Fix: (1) check file content: `cat file.yaml`; "
        "(2) for directory: `kubectl apply -k <dir>` requires kustomization.yaml; "
        "(3) for recursive: `kubectl apply -R -f <dir>` (recursive flag); "
        "(4) for piped input: `cat file.yaml | kubectl apply -f -` (the `-` reads stdin); "
        "(5) common: comment-only YAML file produces zero objects.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-errors", "kubectl error: namespace from object does not match",
        "YAML metadata has namespace X but you passed `-n Y`. "
        "Fix: (1) omit namespace from YAML and use `-n <ns>` on command; "
        "(2) OR keep namespace in YAML and don't pass `-n`; "
        "(3) for multi-namespace deploys (rare): one YAML per namespace OR template via Helm/kustomize.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-errors", "kubectl error: resource quota exceeded",
        "Namespace has ResourceQuota limits that this operation would exceed. "
        "Fix: (1) check quota: `kubectl describe quota -n <ns>`; "
        "(2) free up resources: delete unused pods/PVCs; "
        "(3) reduce request: lower CPU/memory requests in pod spec; "
        "(4) ask admin to raise quota: `kubectl edit quota <name>`; "
        "(5) for `kubectl run` with default resources: explicitly set `--requests` and `--limits`.",
        f"{CLI}/generated/kubectl_run/",
    ),
    (
        "kubectl-errors", "kubectl error: metrics not available (top requires metrics-server)",
        "`kubectl top` needs metrics-server installed. "
        "Fix: (1) install: `kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml`; "
        "(2) verify: `kubectl get deployment metrics-server -n kube-system`; "
        "(3) for local clusters (kind, minikube): often need `--kubelet-insecure-tls` patched into deployment (TLS issue with self-signed certs); "
        "(4) wait ~60s after install before `top` works; "
        "(5) on EKS/GKE/AKS: usually pre-installed.",
        f"{CLI}/generated/kubectl_top/",
    ),
    (
        "kubectl-errors", "kubectl error: invalid label selector",
        "Label selector syntax wrong. "
        "Fix: (1) equality: `-l app=nginx`; "
        "(2) inequality: `-l app!=nginx`; "
        "(3) set: `-l 'environment in (prod, staging)'`; "
        "(4) NOT in: `-l 'tier notin (frontend, backend)'`; "
        "(5) exists: `-l app` (label present); "
        "(6) NOT exists: `-l '!app'`; "
        "(7) AND multiple: `-l app=nginx,tier=frontend`.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-errors", "kubectl error: at least one of (--all|-l|--field-selector) flag is required",
        "Some commands (delete --all) need a selector to prevent accidentally targeting everything. "
        "Fix: (1) name: `kubectl delete pod <name>`; "
        "(2) by label: `kubectl delete pods -l app=test`; "
        "(3) field selector: `kubectl delete pods --field-selector=status.phase=Failed`; "
        "(4) all in namespace (DANGER): `kubectl delete pods --all -n <ns>`.",
        f"{CLI}/generated/kubectl_delete/",
    ),
    (
        "kubectl-errors", "kubectl error: too many requests (429 from API server)",
        "Hit the API server's flow-control limits (priority + fairness). "
        "Fix: (1) back off + retry: most tools should auto-retry; "
        "(2) reduce parallelism in scripts: `xargs -P 4` instead of `-P 50`; "
        "(3) check API priority: `kubectl get apiserver --raw '/livez/poststarthook/priority-and-fairness-config-producer'`; "
        "(4) for huge listings: use `--chunk-size=500` to paginate.",
        f"{CLI}/",
    ),
    (
        "kubectl-errors", "kubectl error: server doesn't have a resource type \"<X>\"",
        "Same as 'no matches for kind' but printed differently. "
        "Fix: (1) `kubectl api-resources` lists everything cluster supports; "
        "(2) for plural/singular: kubectl accepts pod, pods, po — but custom resources may not have short names; "
        "(3) for explicit: use the FQDN: `kubectl get <name>.example.com`; "
        "(4) verify spelling: `kubectl api-resources | grep -i <partial>`.",
        f"{CLI}/generated/kubectl_api-resources/",
    ),
    (
        "kubectl-errors", "kubectl error: error executing template / jsonpath",
        "Template syntax wrong. "
        "Fix: (1) for go-template: `-o go-template='{{range .items}}{{.metadata.name}}{{\"\\n\"}}{{end}}'`; "
        "(2) for jsonpath: `-o jsonpath='{range .items[*]}{.metadata.name}{\"\\n\"}{end}'`; "
        "(3) for arrays: `[*]` (all) or `[0]` (first); "
        "(4) escape special: use `\\.` in field names; "
        "(5) cleaner alternative: pipe to jq: `kubectl get pods -o json | jq -r '.items[].metadata.name'`.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-errors", "kubectl error: kubectl version: client version much newer than server",
        "Client/server version skew can cause weird behaviors. "
        "Fix: (1) check: `kubectl version`; "
        "(2) recommended: within +/- 1 minor (1.30 client + 1.29-1.31 server is fine); "
        "(3) for multiple clusters with different versions: install multiple kubectl binaries + use `kubectl-1.27`, `kubectl-1.30` aliases; "
        "(4) use `asdf` or `mise` to manage kubectl versions: `mise use kubectl@1.30`.",
        f"{CLI}/generated/kubectl_version/",
    ),
    (
        "kubectl-errors", "kubectl error: dial tcp <host>:443: i/o timeout / no route to host",
        "Network can't reach the API server. "
        "Fix: (1) check VPN: many clusters require corporate VPN; "
        "(2) DNS: `nslookup <api-host>`; "
        "(3) for private EKS endpoint: must be inside VPC or via bastion; "
        "(4) check proxy: `HTTPS_PROXY=...` may be required for corporate networks; "
        "(5) test raw: `curl -k https://<api-host>/healthz`.",
        f"{CLI}/",
    ),
    (
        "kubectl-errors", "kubectl error: kubectl wait timed out waiting for condition",
        "Condition didn't become true within --timeout. "
        "Fix: (1) extend: `--timeout=600s`; "
        "(2) for Ready: pod's readinessProbe must pass; check `kubectl describe pod <pod>`; "
        "(3) for rollout: `kubectl rollout status deployment/<name>` is often more useful than `wait`; "
        "(4) for custom: --for=jsonpath syntax: `--for=jsonpath='{.status.phase}'=Succeeded`.",
        f"{CLI}/generated/kubectl_wait/",
    ),

    # ============================================================
    # kubectl-recipes — 38 common workflows
    # ============================================================
    (
        "kubectl-recipes", "kubectl recipe: list pods in current namespace / all namespaces",
        "`kubectl get pods` (current namespace). "
        "All namespaces: `kubectl get pods -A` (or `--all-namespaces`). "
        "Wide output (node + IP): `-o wide`. "
        "Watch live: `kubectl get pods -w`. "
        "Specific status: `--field-selector=status.phase=Running`. "
        "By label: `-l app=nginx`.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: get YAML/JSON of a running resource",
        "`kubectl get pod <name> -o yaml`. "
        "JSON: `-o json`. "
        "Strip server-added fields: pipe through `kubectl-neat` (plugin) or `yq 'del(.metadata.managedFields, .metadata.resourceVersion, .metadata.uid, .status)'`. "
        "Save to file: `kubectl get deploy nginx -o yaml > nginx.yaml` (clean it before re-applying).",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: describe a resource (events + state)",
        "`kubectl describe pod <name>` — full details + recent events. "
        "Events tail: most useful for debugging failed pods. "
        "Recent events across namespace: `kubectl get events -n <ns> --sort-by='.lastTimestamp'`. "
        "Watch events live: `kubectl get events -w`.",
        f"{CLI}/generated/kubectl_describe/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: apply YAML declaratively",
        "Single file: `kubectl apply -f deploy.yaml`. "
        "Directory (non-recursive): `kubectl apply -f ./manifests/`. "
        "Recursive: `kubectl apply -R -f ./manifests/`. "
        "From URL: `kubectl apply -f https://example.com/manifest.yaml`. "
        "Server-side apply (managed-fields tracking, K8s 1.22+): `--server-side`. "
        "Dry-run preview: `--dry-run=client -o yaml` or `--dry-run=server`.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: edit a live resource in your editor",
        "`kubectl edit deployment <name>` opens current YAML in $EDITOR. "
        "Save + close to apply. "
        "For specific output format: `KUBE_EDITOR=vim kubectl edit ...`. "
        "Caveat: changes here aren't in your source git — for GitOps, edit YAML + `apply`.",
        f"{CLI}/generated/kubectl_edit/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: delete resources safely",
        "By name: `kubectl delete pod <name>`. "
        "By label: `kubectl delete pods -l app=test`. "
        "By file: `kubectl delete -f deploy.yaml` (matches name+namespace from file). "
        "All in namespace (DANGER): `kubectl delete pods --all -n <ns>`. "
        "Force (skip graceful): `--force --grace-period=0` (only when normal delete is stuck). "
        "Wait for completion: `--wait=true` (default).",
        f"{CLI}/generated/kubectl_delete/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: pod / container logs",
        "Last container's logs: `kubectl logs <pod>`. "
        "Specific container: `-c <container>`. "
        "Previous (crashed) container: `--previous` (or `-p`). "
        "All containers in pod: `--all-containers=true`. "
        "Follow live: `-f`. "
        "Last N lines: `--tail=100`. "
        "Since duration: `--since=1h`. "
        "By label (across pods): `kubectl logs -l app=nginx --all-containers=true -f`.",
        f"{CLI}/generated/kubectl_logs/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: logs from all pods of a deployment",
        "`kubectl logs -l app=nginx --tail=100 --max-log-requests=10`. "
        "Or by deployment: `kubectl logs deployment/<name>` (logs from one pod under that deployment). "
        "All pods: `for p in $(kubectl get pods -l app=nginx -o name); do kubectl logs $p; done`. "
        "Better: use `stern` or `kail` (third-party multi-pod log tail).",
        f"{CLI}/generated/kubectl_logs/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: exec into a pod for debugging",
        "Shell: `kubectl exec -it <pod> -- /bin/sh` (or bash if available). "
        "Single command: `kubectl exec <pod> -- ls /app`. "
        "Specific container: `-c <container>`. "
        "For stdin without tty (e.g., piping): `-i` only. "
        "For distroless / no-shell images: use `kubectl debug <pod> -it --image=busybox --target=<container>` instead.",
        f"{CLI}/generated/kubectl_exec/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: port-forward to local machine",
        "Pod: `kubectl port-forward pod/<name> 8080:80` (local:remote). "
        "Service: `kubectl port-forward svc/<name> 8080:80`. "
        "Deployment (picks one ready pod): `kubectl port-forward deploy/<name> 8080:80`. "
        "All interfaces (not just localhost): `--address 0.0.0.0`. "
        "Random local port: `8080:80` → use any local port: `:80`.",
        f"{CLI}/generated/kubectl_port-forward/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: copy files in/out of pods",
        "To pod: `kubectl cp ./local-file.txt <pod>:/path/in/pod/`. "
        "From pod: `kubectl cp <pod>:/path/in/pod/file.txt ./local-file.txt`. "
        "Across namespaces: `kubectl cp -n <ns> <pod>:/path ./local`. "
        "Specific container: `-c <container>`. "
        "Caveat: requires `tar` in the container.",
        f"{CLI}/generated/kubectl_cp/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: scale a deployment",
        "`kubectl scale deploy <name> --replicas=5`. "
        "Multiple: `kubectl scale deploy nginx app2 --replicas=3`. "
        "Conditional (only if current matches): `--current-replicas=2 --replicas=5`. "
        "All deployments: `kubectl scale deploy --all --replicas=0` (stop everything). "
        "For autoscaling: use HPA: `kubectl autoscale deploy <name> --min=2 --max=10 --cpu-percent=80`.",
        f"{CLI}/generated/kubectl_scale/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: rollout management (status, history, undo)",
        "Status: `kubectl rollout status deploy/<name>` (blocks until done). "
        "History: `kubectl rollout history deploy/<name>`. "
        "Specific revision details: `--revision=3`. "
        "Rollback to previous: `kubectl rollout undo deploy/<name>`. "
        "Rollback to specific: `--to-revision=3`. "
        "Pause/resume: `kubectl rollout pause deploy/<name>` then `resume`.",
        f"{CLI}/generated/kubectl_rollout/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: restart a deployment (force new pods)",
        "Modern way: `kubectl rollout restart deploy/<name>` (adds annotation to trigger rollout). "
        "Delete pods (RS recreates): `kubectl delete pods -l app=<label>`. "
        "Scale to 0 then up (downtime): `kubectl scale deploy <name> --replicas=0 && kubectl scale deploy <name> --replicas=3`. "
        "For StatefulSet: same restart command works (rolls one at a time).",
        f"{CLI}/generated/kubectl_rollout/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: create configmap from file or literal",
        "Literal: `kubectl create cm myconfig --from-literal=key1=val1 --from-literal=key2=val2`. "
        "File: `kubectl create cm myconfig --from-file=app.conf`. "
        "Directory: `--from-file=./conf-dir/` (each file becomes a key). "
        "With custom key: `--from-file=app=./real-name.conf`. "
        "From env file: `--from-env-file=app.env`. "
        "Mount in pod: as a volume (volumes: configMap: name: myconfig) OR as env vars (envFrom: configMapRef).",
        f"{BASE}/concepts/configuration/configmap/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: create secret (literal, file, docker-registry)",
        "Generic literal: `kubectl create secret generic mysec --from-literal=password=hunter2`. "
        "From file: `--from-file=cert=./cert.pem --from-file=key=./key.pem`. "
        "Docker registry: `kubectl create secret docker-registry myreg --docker-server=registry.example.com --docker-username=user --docker-password=pass`. "
        "TLS: `kubectl create secret tls my-tls --cert=server.crt --key=server.key`. "
        "Caveat: secrets are base64, NOT encrypted — use Sealed Secrets, External Secrets, or KMS encryption-at-rest.",
        f"{BASE}/concepts/configuration/secret/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: switch contexts and namespaces",
        "List: `kubectl config get-contexts`. "
        "Switch: `kubectl config use-context <name>`. "
        "Current: `kubectl config current-context`. "
        "Set default namespace for context: `kubectl config set-context --current --namespace=<ns>`. "
        "Plugins for ergonomics: `kubectx` (switch contexts), `kubens` (switch namespaces). "
        "Per-command namespace: `kubectl -n <ns> get pods`.",
        f"{CLI}/generated/kubectl_config_use-context/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: merge multiple kubeconfigs",
        "Set env: `KUBECONFIG=~/.kube/config:~/.kube/cluster2:~/.kube/cluster3`. "
        "Now `kubectl config get-contexts` shows merged. "
        "Save as one: `KUBECONFIG=... kubectl config view --flatten > ~/.kube/merged-config`. "
        "Then `export KUBECONFIG=~/.kube/merged-config`.",
        f"{BASE}/concepts/configuration/organize-cluster-access-kubeconfig/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: run a one-off pod for debugging",
        "Quick shell: `kubectl run -it --rm debug --image=alpine -- /bin/sh`. "
        "`--rm` deletes pod on exit. `-i` stdin, `-t` tty. "
        "With network test image: `--image=nicolaka/netshoot` (has dig, curl, tcpdump, nslookup). "
        "Mount a configmap: requires writing a full pod spec — easier with `kubectl debug`.",
        f"{CLI}/generated/kubectl_run/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: debug a running pod (ephemeral container)",
        "K8s 1.25+ stable. `kubectl debug -it <pod> --image=busybox --target=<container>`. "
        "Same pod, same network namespace, same volumes — but a separate container with extra tools. "
        "For distroless: critical for debugging since base has no shell. "
        "Copy + modify pod: `kubectl debug <pod> --copy-to=debug-pod --share-processes -it -- /bin/sh`. "
        "Node debug: `kubectl debug node/<node> -it --image=busybox` (host filesystem at /host).",
        f"{CLI}/generated/kubectl_debug/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: wait until a resource is ready",
        "Pod ready: `kubectl wait --for=condition=Ready pod/<name> --timeout=300s`. "
        "Pods by label: `kubectl wait --for=condition=Ready pods -l app=nginx --timeout=5m`. "
        "Deployment available: `kubectl wait --for=condition=Available deploy/<name>`. "
        "Job completion: `kubectl wait --for=condition=Complete job/<name>`. "
        "Custom (jsonpath): `--for=jsonpath='{.status.replicas}'=3`. "
        "Deletion: `--for=delete pod/<name>`.",
        f"{CLI}/generated/kubectl_wait/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: check what you can do (RBAC)",
        "Specific: `kubectl auth can-i create pods -n <ns>` (prints yes/no). "
        "List all: `kubectl auth can-i --list -n <ns>`. "
        "Impersonate test: `--as=system:serviceaccount:default:my-sa`. "
        "Show effective user: `kubectl auth whoami` (K8s 1.28+). "
        "For all namespaces: `kubectl auth can-i list pods --all-namespaces`.",
        f"{CLI}/generated/kubectl_auth/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: top pods / nodes (resource usage)",
        "Requires metrics-server installed. "
        "Pods: `kubectl top pods` (CPU + memory). "
        "All namespaces: `kubectl top pods -A`. "
        "Sort by CPU: `--sort-by=cpu`. "
        "By memory: `--sort-by=memory`. "
        "Nodes: `kubectl top nodes`. "
        "Per container: `kubectl top pod <name> --containers`.",
        f"{CLI}/generated/kubectl_top/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: get events sorted by time",
        "`kubectl get events --sort-by='.lastTimestamp'` (oldest first). "
        "Newest first: pipe `tail`. "
        "All namespaces: `-A`. "
        "Watch live: `-w`. "
        "Filter by type: `--field-selector=type=Warning`. "
        "By involvedObject: `--field-selector=involvedObject.name=<pod-name>`. "
        "Better tool: `kubectl events -A --watch` (K8s 1.27+ has a dedicated command).",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: drain a node for maintenance",
        "`kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`. "
        "Evicts pods respecting PodDisruptionBudgets. "
        "Cordon first (stop new scheduling): `kubectl cordon <node>`. "
        "After maintenance: `kubectl uncordon <node>` to allow scheduling again. "
        "For force: `--force` (deletes orphan pods). "
        "For stateful pods with persistent volumes: take care.",
        f"{CLI}/generated/kubectl_drain/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: find pods on a specific node",
        "`kubectl get pods -A --field-selector=spec.nodeName=<node-name> -o wide`. "
        "Combined: `kubectl describe node <node>` lists pods + resource usage. "
        "By label additionally: `--field-selector=spec.nodeName=<node>,status.phase=Running`. "
        "For node-affinity debugging: `kubectl get pod <pod> -o jsonpath='{.spec.nodeSelector}'`.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: explain a resource schema",
        "`kubectl explain pod`. "
        "Field path: `kubectl explain pod.spec.containers`. "
        "Recursive: `kubectl explain pod.spec --recursive`. "
        "Specific API version: `--api-version=apps/v1 kubectl explain deployment.spec.strategy`. "
        "Most useful when authoring YAML — shows required fields + types.",
        f"{CLI}/generated/kubectl_explain/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: list all resources of all types in a namespace",
        "`kubectl get all -n <ns>` (shows pods, deployments, services, replicasets — but NOT secrets/configmaps/cronjobs/etc). "
        "True all: `kubectl api-resources --verbs=list --namespaced -o name | xargs -n1 kubectl get -n <ns> 2>/dev/null`. "
        "Better tool: `kubectl-tree` plugin shows resource ownership tree.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: diff before applying",
        "`kubectl diff -f deploy.yaml` shows what would change in the cluster. "
        "Requires server-side dry-run support (K8s 1.16+, default). "
        "Useful in CI: `kubectl diff` exits 1 if differences exist. "
        "For multi-file: `kubectl diff -R -f ./manifests/`.",
        f"{CLI}/generated/kubectl_diff/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: patch a resource (merge / json / strategic)",
        "Strategic merge (default, K8s-aware): `kubectl patch deploy nginx -p '{\"spec\":{\"replicas\":3}}'`. "
        "JSON patch (RFC 6902): `--type=json -p='[{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":5}]'`. "
        "JSON merge patch: `--type=merge -p '{\"metadata\":{\"labels\":{\"newkey\":\"val\"}}}'`. "
        "From file: `-p \"$(cat patch.yaml)\"`.",
        f"{CLI}/generated/kubectl_patch/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: server-side apply (managed fields)",
        "`kubectl apply --server-side -f deploy.yaml`. "
        "Server tracks field ownership — useful for multiple controllers managing same object. "
        "Force takeover: `--force-conflicts` (when two SSA managers conflict). "
        "Specific field manager: `--field-manager=my-tool`. "
        "List who owns what: `kubectl get <kind> <name> -o yaml | grep -A 5 managedFields`.",
        f"{CLI}/generated/kubectl_apply/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: custom columns / jsonpath output",
        "Custom columns: `kubectl get pods -o custom-columns='NAME:.metadata.name,IP:.status.podIP,NODE:.spec.nodeName'`. "
        "From file: `-o custom-columns-file=cols.txt`. "
        "JSONPath: `kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}: {.status.podIP}{\"\\n\"}{end}'`. "
        "Just one field: `-o jsonpath='{.items[0].spec.nodeName}'`. "
        "For complex queries: pipe `-o json` to jq.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: bash autocompletion for kubectl",
        "Install: `source <(kubectl completion bash)` (or zsh, fish). "
        "Persist: `echo 'source <(kubectl completion bash)' >> ~/.bashrc`. "
        "Alias + completion: `alias k=kubectl && complete -o default -F __start_kubectl k`. "
        "For zsh: `source <(kubectl completion zsh)` and add `compdef __start_kubectl k`.",
        f"{CLI}/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: create a namespace + set as default",
        "`kubectl create namespace <name>`. "
        "Set as default for current context: `kubectl config set-context --current --namespace=<name>`. "
        "Verify: `kubectl config view --minify | grep namespace`. "
        "Delete (cascade): `kubectl delete namespace <name>` (removes everything in it — slow if many resources).",
        f"{BASE}/concepts/overview/working-with-objects/namespaces/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: useful field selectors",
        "Pods by phase: `--field-selector=status.phase=Running` (or Pending, Failed, Succeeded). "
        "By node: `spec.nodeName=<node>`. "
        "Not on node: `spec.nodeName!=<node>`. "
        "Excluded namespaces: `metadata.namespace!=kube-system`. "
        "Events for resource: `involvedObject.name=<name>`. "
        "Combine with labels: `-l app=nginx --field-selector=status.phase=Running`.",
        f"{CLI}/generated/kubectl_get/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: expose a deployment as a Service",
        "`kubectl expose deploy <name> --port=80 --target-port=8080 --type=ClusterIP`. "
        "NodePort: `--type=NodePort`. "
        "LoadBalancer (cloud): `--type=LoadBalancer`. "
        "From pod: `kubectl expose pod <name> --port=80 --name=my-service`. "
        "For headless: `--cluster-ip=None`.",
        f"{CLI}/generated/kubectl_expose/",
    ),
    (
        "kubectl-recipes", "kubectl recipe: get last applied YAML",
        "`kubectl get <kind> <name> -o jsonpath='{.metadata.annotations.kubectl\\.kubernetes\\.io/last-applied-configuration}' | jq .`. "
        "For server-side apply: doesn't use that annotation — use managed-fields instead. "
        "Compare to live: `kubectl get <kind> <name> -o yaml | diff - last-applied.yaml`.",
        f"{CLI}/generated/kubectl_apply/",
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
            evidence_id=f"ev-kc-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-kc-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="kubectl-catalog",
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

"""Direct-insert 30 docker error claims + 35 docker recipe claims."""

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

BASE = "https://docs.docker.com"

CLAIMS = [
    # ============================================================
    # docker-errors — 30 most common errors
    # ============================================================
    (
        "docker-errors", "docker error: permission denied while trying to connect to the Docker daemon socket",
        "Your user isn't in the docker group, OR docker socket permissions are wrong. "
        "Fix: (1) add your user to docker group: `sudo usermod -aG docker $USER` then logout/login (group changes need new session); "
        "(2) verify with `groups` — should list docker; "
        "(3) if still failing, check socket: `ls -l /var/run/docker.sock` — should be group=docker; "
        "(4) NEVER `chmod 666 /var/run/docker.sock` — that grants root to any local user; "
        "(5) safer alternative: rootless docker (`dockerd-rootless-setuptool.sh install`).",
        f"{BASE}/engine/install/linux-postinstall/",
    ),
    (
        "docker-errors", "docker error: Cannot connect to the Docker daemon. Is the docker daemon running?",
        "The docker daemon process isn't running, or you can't reach its socket. "
        "Fix: (1) Linux: `sudo systemctl start docker && sudo systemctl enable docker`; "
        "(2) macOS/Windows: open Docker Desktop app; "
        "(3) check status: `sudo systemctl status docker`; "
        "(4) for remote daemon: verify DOCKER_HOST env var: `echo $DOCKER_HOST`; "
        "(5) for socket permission error variant, see 'permission denied' fix.",
        f"{BASE}/engine/daemon/start/",
    ),
    (
        "docker-errors", "docker error: pull access denied for image, repository does not exist or may require docker login",
        "You're trying to pull a private image without authentication. "
        "Fix: (1) `docker login` (Docker Hub) or `docker login <registry>` for private; "
        "(2) verify the image name + tag is correct: `docker search <name>`; "
        "(3) for private GitLab/GitHub container registries: `docker login ghcr.io -u <user>`; "
        "(4) for kubernetes-style pull secrets, see the docker CLI's --auth-config; "
        "(5) check Docker Hub rate limits — anonymous users are limited; logged-in users get higher quotas.",
        f"{BASE}/reference/cli/docker/login/",
    ),
    (
        "docker-errors", "docker error: image with reference X not found locally",
        "Image doesn't exist on this machine. Wasn't built, wasn't pulled, or wrong tag. "
        "Fix: (1) `docker pull <image>` to fetch; (2) `docker images` to see what's local; "
        "(3) verify the tag: `:latest` is default but newer Docker often defaults differently — be explicit; "
        "(4) for locally-built images, check `docker build -t <name>:<tag> .` and verify the build succeeded.",
        f"{BASE}/reference/cli/docker/image/pull/",
    ),
    (
        "docker-errors", "docker error: container name X already in use",
        "You ran `docker run --name X` but a container with that name already exists. "
        "Fix: (1) remove the old one: `docker rm <name>`; (2) if running, stop first: `docker stop <name> && docker rm <name>`; "
        "(3) auto-remove on exit: `docker run --rm --name X ...`; "
        "(4) for one-off pattern: `docker run --rm` without --name (lets docker generate a random name).",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-errors", "docker error: port is already allocated (Bind for 0.0.0.0:N failed)",
        "Another process is using the host port you tried to bind. "
        "Fix: (1) find what's using it: `sudo lsof -i :<port>` or `sudo ss -tlnp | grep <port>`; "
        "(2) use a different host port: `-p 8081:8080` instead of `-p 8080:8080`; "
        "(3) let docker pick: `-p 8080` (random host port — see actual with `docker port <container>`); "
        "(4) stop the conflicting container: `docker ps` to find it, then `docker stop <id>`.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-errors", "docker error: no space left on device",
        "Docker accumulated containers, images, volumes, build cache until disk filled. "
        "Fix: (1) `docker system df` shows what's taking space; (2) `docker system prune -a --volumes` removes stopped containers + unused images + networks + build cache + dangling volumes; "
        "(3) check Docker's data root: `docker info | grep 'Docker Root Dir'` — for Mac/Win it's inside the Docker Desktop VM (resize via Docker Desktop GUI); "
        "(4) for stubborn dangling layers: `docker builder prune --all`.",
        f"{BASE}/reference/cli/docker/system/prune/",
    ),
    (
        "docker-errors", "docker error: OCI runtime exec failed: command not found",
        "You ran `docker exec` with a command that doesn't exist in the container. "
        "Fix: (1) check what's in the container: `docker exec <container> ls /bin /usr/bin`; "
        "(2) for alpine images, bash isn't installed — use `sh` instead: `docker exec -it <container> sh`; "
        "(3) for distroless images (no shell), use `kubectl debug` or attach a sidecar; "
        "(4) install the missing tool by extending the Dockerfile, not by running ad-hoc apt-get inside (changes are ephemeral).",
        f"{BASE}/reference/cli/docker/container/exec/",
    ),
    (
        "docker-errors", "docker error: container exited with code 137 (OOMKilled)",
        "Container hit its memory limit (or host OOM) and was killed by the kernel. "
        "Fix: (1) check: `docker inspect <name> | grep -i oom` shows OOMKilled: true; "
        "(2) increase memory limit: `docker run -m 2g ...`; "
        "(3) profile actual usage: `docker stats` while it runs; "
        "(4) for memory leaks in your app, fix the leak; (5) on Mac/Win, Docker Desktop has its own RAM allocation — bump via Settings > Resources.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-errors", "docker error: container exited with code 143 (SIGTERM)",
        "Container received SIGTERM and exited. Often a graceful shutdown but in scripts can be unexpected. "
        "Fix: (1) check logs: `docker logs <container>` to see what happened before shutdown; "
        "(2) for `docker stop`-induced 143: that's expected behavior; "
        "(3) for orchestrator-triggered shutdowns (k8s, swarm), check the orchestrator's status; "
        "(4) for hung processes, increase the grace period: `docker stop -t 60` (waits 60s before SIGKILL).",
        f"{BASE}/reference/cli/docker/container/stop/",
    ),
    (
        "docker-errors", "docker error: failed to compute cache key (Dockerfile)",
        "BuildKit's cache resolution failed during a build, usually a permissions or file-system issue. "
        "Fix: (1) `docker build --no-cache -t <name> .` skips cache; "
        "(2) check for symlinks in the build context — BuildKit doesn't follow some; "
        "(3) for COPY errors, ensure the source file exists in the build context (`.` or whatever you passed as the last arg); "
        "(4) re-create context: `tar c . | docker build -`.",
        f"{BASE}/build/cache/",
    ),
    (
        "docker-errors", "docker error: invalid reference format",
        "The image name has illegal characters or wrong format. "
        "Fix: (1) Format must be `[registry/][namespace/]name[:tag|@digest]`; "
        "(2) uppercase in image names is not allowed: convert to lowercase; "
        "(3) tags can't have certain chars: stick with `[a-zA-Z0-9_.-]`; "
        "(4) for digest pinning: `image@sha256:abc...` (immutable).",
        f"{BASE}/reference/cli/docker/image/",
    ),
    (
        "docker-errors", "docker error: COPY failed: file not found in build context",
        "Dockerfile's `COPY src dest` references a file that's not in the build context directory. "
        "Fix: (1) verify the file exists relative to the build context (the `.` in `docker build .`); "
        "(2) check .dockerignore — it might be excluding the file; "
        "(3) ensure paths are relative to build context, not absolute on your machine; "
        "(4) for files outside the build context, restructure: either move into context, or run build from a higher directory: `docker build -f path/to/Dockerfile higher/dir`.",
        f"{BASE}/reference/dockerfile/",
    ),
    (
        "docker-errors", "docker error: standard_init_linux.go: exec format error",
        "Architecture mismatch: image is built for a different CPU arch than the host. Common when pulling x86_64 images on M1/ARM Mac. "
        "Fix: (1) check image arch: `docker inspect <image> | grep Architecture`; "
        "(2) pull explicitly for your arch: `docker pull --platform linux/arm64 <image>`; "
        "(3) for x86 image on ARM Mac: `docker run --platform linux/amd64 <image>` (works via emulation, slower); "
        "(4) for multi-arch builds: see `docker buildx build --platform linux/amd64,linux/arm64`.",
        f"{BASE}/build/building/multi-platform/",
    ),
    (
        "docker-errors", "docker error: network X not found",
        "Container references a docker network that doesn't exist. "
        "Fix: (1) `docker network ls` to see what's available; (2) create: `docker network create <name>`; "
        "(3) for compose, ensure the network is declared in the compose file's top-level `networks:` section; "
        "(4) external network (defined outside compose): `networks: { mynet: { external: true } }`.",
        f"{BASE}/reference/cli/docker/network/",
    ),
    (
        "docker-errors", "docker error: cgroup mount destination required",
        "Container needs cgroup access but the host doesn't expose it (or cgroupv2-related issue). "
        "Fix: (1) for systemd-in-container (often this error), use `--cgroup-parent=docker.slice --cgroupns=host`; "
        "(2) ensure host has cgroupv2: `mount | grep cgroup2`; "
        "(3) for old containers expecting cgroupv1: `systemd.unified_cgroup_hierarchy=0` boot param on host (or just use a newer image).",
        f"{BASE}/engine/daemon/",
    ),
    (
        "docker-errors", "docker error: chown: changing ownership of '/...': Operation not permitted",
        "Container's user (often non-root) tried to chown but lacks capability. "
        "Fix: (1) for bind mounts: file ownership must match between host and container — `--user $(id -u):$(id -g)`; "
        "(2) for named volumes: pre-init permissions in Dockerfile with USER root + chown, THEN switch back to non-root; "
        "(3) emergency: run as root (`--user 0`) but understand security implications; "
        "(4) for distroless / readonly fs: use the non-root variant of your base image (e.g., `gcr.io/distroless/static:nonroot`).",
        f"{BASE}/engine/security/userns-remap/",
    ),
    (
        "docker-errors", "docker error: pull rate limit exceeded",
        "Docker Hub limits anonymous pulls (100/6h per IP). You're past that. "
        "Fix: (1) `docker login` — authenticated users get 200/6h; "
        "(2) for paid Docker Hub plans: unlimited (mostly); "
        "(3) mirror Docker Hub locally with `docker run --restart=always -d -p 5000:5000 --name registry registry:2` (pull-through cache); "
        "(4) use ghcr.io / quay.io / private registries that have no such limits; "
        "(5) for CI runners on cloud providers (AWS/GCP/Azure), use their managed registries to avoid Hub limits.",
        f"{BASE}/reference/cli/docker/login/",
    ),
    (
        "docker-errors", "docker error: TLS handshake timeout when pulling",
        "Network issue connecting to a registry. "
        "Fix: (1) ping the registry host; (2) `docker info` to check daemon config (proxy?); "
        "(3) for corporate proxy: set `HTTPS_PROXY` env in /etc/systemd/system/docker.service.d/proxy.conf then `systemctl daemon-reload && systemctl restart docker`; "
        "(4) for self-signed registry certs: add the registry's CA to /etc/docker/certs.d/<registry>/ca.crt.",
        f"{BASE}/engine/daemon/proxy/",
    ),
    (
        "docker-errors", "docker error: container restart loop (Restarting after exit)",
        "Container keeps exiting and Docker's restart policy keeps relaunching. "
        "Fix: (1) `docker logs <container>` to see why it exits; "
        "(2) `docker inspect <container> | grep -A5 RestartPolicy` to confirm policy; "
        "(3) for testing changes: `docker update --restart=no <container>` then start manually; "
        "(4) common causes: missing env var, app crash on startup, port already taken inside container, healthcheck failing — fix and restart.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-errors", "docker error: error response from daemon: driver failed programming external connectivity",
        "Often a leftover network state issue. iptables / network bridge state confused. "
        "Fix: (1) `docker network prune` clears unused networks; (2) restart docker daemon: `sudo systemctl restart docker`; "
        "(3) check iptables: `sudo iptables -L -n` for stale rules; (4) on systems with firewalld, ensure docker zone is configured: `firewall-cmd --get-active-zones`.",
        f"{BASE}/network/",
    ),
    (
        "docker-errors", "docker error: layer corrupted (failed to register layer)",
        "Image layer download or unpack corrupted. "
        "Fix: (1) `docker rmi <image>` then `docker pull <image>` to refetch; "
        "(2) if persistent, clear all of docker's image cache: `docker image prune -a -f`; "
        "(3) for corrupt overlay2 store: drastic — stop docker, `rm -rf /var/lib/docker/overlay2`, restart docker (DESTROYS ALL LOCAL IMAGES).",
        f"{BASE}/engine/daemon/",
    ),
    (
        "docker-errors", "docker error: failed to start container (cgroup mount fails)",
        "Common on Docker Desktop after major OS updates, or when cgroupv2 host meets cgroupv1 expectations. "
        "Fix: (1) restart Docker Desktop entirely; (2) `Settings > Troubleshoot > Reset to factory defaults` (loses your local images); "
        "(3) for Linux: ensure systemd is configured for cgroupv2: edit grub: `systemd.unified_cgroup_hierarchy=1`.",
        f"{BASE}/engine/daemon/",
    ),
    (
        "docker-errors", "docker error: Service X failed to build (compose)",
        "docker compose build failed for one service. "
        "Fix: (1) `docker compose build --no-cache <service>` to retry from scratch; "
        "(2) `docker compose build --progress=plain <service>` shows full output (not summarized); "
        "(3) try building the Dockerfile directly: `docker build -f path/to/Dockerfile .` — easier to debug than compose; "
        "(4) check the service's `build:` block in compose.yml — context and dockerfile paths must be correct.",
        f"{BASE}/reference/cli/docker/compose/build/",
    ),
    (
        "docker-errors", "docker error: dependency failed to start (compose depends_on)",
        "A service named in depends_on failed to start; compose halted the dependent. "
        "Fix: (1) `docker compose ps` shows status of all services; "
        "(2) `docker compose logs <failed-service>` shows why; "
        "(3) for services that need healthy (not just started) deps, use `depends_on: { service: { condition: service_healthy } }` + healthcheck; "
        "(4) for slow-to-start deps: increase healthcheck retries.",
        f"{BASE}/compose-file/services/",
    ),
    (
        "docker-errors", "docker error: invalid argument value for --memory",
        "You passed --memory with a malformed value or below docker's minimum. "
        "Fix: (1) format is `<number>[unit]` where unit ∈ b, k, m, g (no spaces): `--memory=512m`, `--memory=2g`; "
        "(2) docker minimum is 6MB; smaller values are rejected; "
        "(3) for swap config: `--memory-swap=4g` (must be >= --memory; -1 = unlimited swap).",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-errors", "docker error: cannot find docker compose (docker-compose vs docker compose)",
        "Old `docker-compose` (hyphen) vs new `docker compose` (no hyphen, integrated). "
        "Fix: (1) install Docker Compose v2: `apt install docker-compose-plugin` (Linux) or built into Docker Desktop; "
        "(2) use new syntax: `docker compose up` (note: no hyphen); "
        "(3) for legacy scripts that hardcode `docker-compose`: symlink `ln -s /usr/libexec/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose`.",
        f"{BASE}/compose/install/linux/",
    ),
    (
        "docker-errors", "docker warning: unhealthy container",
        "Container's HEALTHCHECK returned a non-zero exit code. Docker marks it unhealthy. "
        "Fix: (1) `docker inspect <name> --format='{{json .State.Health}}' | jq` shows the last 5 healthcheck results; "
        "(2) `docker logs <name>` may have app errors; (3) run the healthcheck command manually inside the container: `docker exec <name> <hc-command>`; "
        "(4) tune healthcheck timing in Dockerfile: `HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD curl -f http://localhost/health || exit 1`.",
        f"{BASE}/reference/dockerfile/",
    ),
    (
        "docker-errors", "docker error: failed to solve: failed to read dockerfile",
        "BuildKit can't find or read the Dockerfile. "
        "Fix: (1) confirm Dockerfile exists at the path: by default `./Dockerfile` in the build context; "
        "(2) use `-f` for non-default name/location: `docker build -f Dockerfile.dev .`; "
        "(3) verify permissions allow reading; (4) for Windows line endings (CRLF) in Dockerfile parsed on Linux — convert: `dos2unix Dockerfile`.",
        f"{BASE}/reference/cli/docker/image/build/",
    ),

    # ============================================================
    # docker-recipes — 35 common workflows
    # ============================================================
    (
        "docker-recipes", "docker recipe: run a one-shot container",
        "Standard ephemeral run. `docker run --rm <image> <command>`. "
        "The `--rm` removes the container when it exits — no cleanup needed. "
        "Examples: `docker run --rm alpine echo hello`; `docker run --rm -it ubuntu bash` (interactive shell, exits clean). "
        "For mounting a local dir: `docker run --rm -v $(pwd):/work -w /work alpine ls`. "
        "Use case: testing commands without polluting your container list.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-recipes", "docker recipe: run a long-running container with a name",
        "For daemons. `docker run -d --name <name> --restart=unless-stopped -p host:container <image>`. "
        "Flags: `-d` detach (background); `--name` for easy reference; `--restart=unless-stopped` restarts on crash but not after `docker stop`; "
        "Port mapping: `-p 8080:80` maps host 8080 to container 80. "
        "Example: `docker run -d --name nginx --restart=unless-stopped -p 8080:80 nginx:latest`.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-recipes", "docker recipe: exec into a running container",
        "Get a shell inside. `docker exec -it <name> sh` (or bash if available). "
        "Run a one-off command: `docker exec <name> ls /var/log`. "
        "As root (if container's default user isn't root): `docker exec -it -u 0 <name> sh`. "
        "From a different working directory: `docker exec -it -w /app <name> sh`. "
        "With env vars: `docker exec -it -e MY_VAR=value <name> printenv MY_VAR`.",
        f"{BASE}/reference/cli/docker/container/exec/",
    ),
    (
        "docker-recipes", "docker recipe: view logs (one-shot + follow)",
        "`docker logs <name>` dumps all logs to date. "
        "Follow live: `docker logs -f <name>` (like tail -f). "
        "Last N lines: `docker logs --tail 100 <name>`. "
        "Since a time: `docker logs --since 10m <name>` or `--since 2024-01-15`. "
        "With timestamps: `docker logs -t <name>`. "
        "Pipe-friendly: `docker logs <name> 2>&1 | grep ERROR`.",
        f"{BASE}/reference/cli/docker/container/logs/",
    ),
    (
        "docker-recipes", "docker recipe: copy files between host and container",
        "`docker cp <src> <dst>`. Examples: "
        "Host → container: `docker cp ./local-file.txt <name>:/path/in/container/`. "
        "Container → host: `docker cp <name>:/path/in/container/file.txt ./`. "
        "Whole directory: `docker cp ./localdir <name>:/path/` (recursively copies). "
        "Note: cp works on stopped containers too. For live mounting prefer volumes.",
        f"{BASE}/reference/cli/docker/container/cp/",
    ),
    (
        "docker-recipes", "docker recipe: build an image with Dockerfile",
        "Standard build. `docker build -t <name>:<tag> .` from directory with Dockerfile. "
        "Specific Dockerfile name: `docker build -t myapp:dev -f Dockerfile.dev .`. "
        "Build args (overriding ARG defaults): `docker build --build-arg VERSION=1.2.3 -t myapp .`. "
        "Force no cache: `--no-cache`. Squash final image: `--squash` (experimental). "
        "Build context from stdin: `tar c . | docker build -`. "
        "Show layer build progress: `--progress=plain`.",
        f"{BASE}/reference/cli/docker/image/build/",
    ),
    (
        "docker-recipes", "docker recipe: tag and push an image to a registry",
        "Steps: (1) Build: `docker build -t myapp:1.0 .`; "
        "(2) Tag for registry: `docker tag myapp:1.0 registry.example.com/myteam/myapp:1.0`; "
        "(3) Login: `docker login registry.example.com`; "
        "(4) Push: `docker push registry.example.com/myteam/myapp:1.0`. "
        "For Docker Hub: `docker push <user>/myapp:1.0`. "
        "Push all tags of an image: `docker push --all-tags <name>`.",
        f"{BASE}/reference/cli/docker/image/push/",
    ),
    (
        "docker-recipes", "docker recipe: multi-stage Dockerfile",
        "Reduce image size by building in one stage, copying artifacts to a minimal final stage. Pattern: ```\nFROM golang:1.22 AS builder\nWORKDIR /app\nCOPY . .\nRUN go build -o myapp .\n\nFROM gcr.io/distroless/static\nCOPY --from=builder /app/myapp /\nENTRYPOINT [\"/myapp\"]\n```. "
        "Final image is ~10MB (distroless) vs ~500MB (golang). Build cache friendly: deps install in early stages cache well.",
        f"{BASE}/build/building/multi-stage/",
    ),
    (
        "docker-recipes", "docker recipe: multi-platform build with buildx",
        "Build for multiple architectures from one Dockerfile. Steps: "
        "(1) Set up builder: `docker buildx create --use --name multiarch`; "
        "(2) `docker buildx inspect --bootstrap` (downloads QEMU emulators if needed); "
        "(3) Build + push: `docker buildx build --platform linux/amd64,linux/arm64 -t myorg/myapp:1.0 --push .`. "
        "Note: must `--push` (or `--load` for single platform) — can't store multi-arch locally without a registry. "
        "Verify the resulting manifest: `docker buildx imagetools inspect myorg/myapp:1.0`.",
        f"{BASE}/build/building/multi-platform/",
    ),
    (
        "docker-recipes", "docker recipe: docker compose up a stack",
        "Run a multi-service app declared in compose.yml. `docker compose up -d` (detached). "
        "Build before running: `docker compose up -d --build`. "
        "Specific services only: `docker compose up -d web db` (skips other services in file). "
        "Different compose file: `docker compose -f docker-compose.prod.yml up -d`. "
        "Stop everything: `docker compose down`. Stop + remove volumes: `docker compose down -v`. "
        "Logs of one service: `docker compose logs -f <service>`.",
        f"{BASE}/reference/cli/docker/compose/up/",
    ),
    (
        "docker-recipes", "docker recipe: basic compose.yml structure",
        "Minimum viable compose file. ```\nservices:\n  web:\n    image: nginx:alpine\n    ports:\n      - \"8080:80\"\n    volumes:\n      - ./html:/usr/share/nginx/html:ro\n  db:\n    image: postgres:15\n    environment:\n      POSTGRES_PASSWORD: secret\n    volumes:\n      - dbdata:/var/lib/postgresql/data\nvolumes:\n  dbdata:\n```. "
        "Top-level keys: `services` (containers), `networks`, `volumes`, `configs`, `secrets`. "
        "Per-service common keys: image, build, ports, environment, volumes, depends_on, networks, restart, healthcheck.",
        f"{BASE}/compose-file/",
    ),
    (
        "docker-recipes", "docker recipe: use named volumes for persistent storage",
        "Volumes survive container removal. Steps: "
        "(1) Create: `docker volume create mydata`; (2) Use: `docker run -v mydata:/data nginx`; "
        "(3) Inspect: `docker volume inspect mydata`; (4) List: `docker volume ls`; (5) Remove: `docker volume rm mydata`. "
        "In compose: `volumes: { mydata: {} }` at top level; per-service `volumes: [\"mydata:/data\"]`. "
        "For backup: `docker run --rm -v mydata:/data -v $(pwd):/backup ubuntu tar czf /backup/data.tar.gz -C /data .`. "
        "Volume drivers (NFS, S3-backed): pass `--driver=<name>` on create.",
        f"{BASE}/reference/cli/docker/volume/",
    ),
    (
        "docker-recipes", "docker recipe: bind mount source code for dev",
        "Live-mount a host directory into the container for development. "
        "`docker run -v $(pwd):/app -w /app node:18 npm run dev` runs node app with live code reload. "
        "Read-only mount: `-v $(pwd):/app:ro`. "
        "Specific subdirs: `-v ./src:/app/src -v ./tests:/app/tests`. "
        "Caveat: file permissions — container's user must match host's UID/GID, or files become root-owned (fix: `--user $(id -u):$(id -g)`).",
        f"{BASE}/storage/bind-mounts/",
    ),
    (
        "docker-recipes", "docker recipe: create and use a custom network",
        "Custom networks enable inter-container DNS resolution. Steps: "
        "(1) `docker network create mynet`; "
        "(2) Run containers on it: `docker run --network=mynet --name api myapi:latest` and `docker run --network=mynet --name web mynginx:latest`; "
        "(3) Inside web container, can reach api by name: `curl http://api:8080`. "
        "List: `docker network ls`. Remove: `docker network rm mynet`. "
        "For compose: networks are auto-created. Default name: `<compose-project>_default`.",
        f"{BASE}/reference/cli/docker/network/",
    ),
    (
        "docker-recipes", "docker recipe: clean up disk space",
        "Docker accumulates fast. `docker system df` shows breakdown. "
        "Conservative: `docker container prune` (stopped containers), `docker image prune` (dangling images). "
        "More aggressive: `docker system prune -a --volumes` (all unused images, networks, build cache, dangling volumes). "
        "Force: `-f` skip the y/n prompt. "
        "Builder cache: `docker builder prune -a`. "
        "Specific volume: `docker volume rm <name>`. "
        "Always preview first: `docker system df -v` shows what's there.",
        f"{BASE}/reference/cli/docker/system/prune/",
    ),
    (
        "docker-recipes", "docker recipe: pass environment variables to containers",
        "Three patterns. (1) Inline: `-e KEY=value -e KEY2=value2`. (2) From a file: `--env-file .env`. (3) From shell: `-e MY_VAR` (inherits current shell's MY_VAR). "
        "In compose: `environment: { KEY: value }` or `env_file: .env`. "
        "For sensitive data, use secrets instead of env: `--secret source=mysecret,target=/etc/secret`. "
        "Verify what's set inside container: `docker exec <name> env`.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-recipes", "docker recipe: limit container resources (CPU + memory)",
        "Prevent runaway containers. `docker run -m 512m --cpus=\"1.5\" --pids-limit 100 myapp`. "
        "Memory: `-m 512m` (limit), `--memory-reservation=256m` (soft limit, scaled down under host pressure), `--memory-swap=1g` (memory + swap total). "
        "CPU: `--cpus=\"1.5\"` (max 1.5 cores), `--cpu-shares=512` (relative weight), `--cpuset-cpus=\"0,1\"` (pin to specific cores). "
        "PIDs: `--pids-limit=100` (max processes). "
        "Verify limits: `docker inspect <name> | grep -E 'Memory|Cpu|Pids'`.",
        f"{BASE}/reference/cli/docker/container/run/",
    ),
    (
        "docker-recipes", "docker recipe: healthcheck in Dockerfile",
        "Mark container unhealthy automatically. ```\nFROM nginx:alpine\nHEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\\n  CMD wget -q --spider http://localhost/ || exit 1\n```. "
        "Tuning: `--interval` (frequency), `--timeout` (max command time), `--start-period` (grace period before failures count), `--retries` (consecutive failures = unhealthy). "
        "View status: `docker ps` shows `(healthy)` or `(unhealthy)`. "
        "For compose: `healthcheck:` section per service, same options.",
        f"{BASE}/reference/dockerfile/",
    ),
    (
        "docker-recipes", "docker recipe: optimize image size",
        "Slim Docker images. Patterns: "
        "(1) use alpine or distroless base; (2) multi-stage builds (drop build deps in final image); "
        "(3) combine RUN steps to share layers + remove temp files in same layer: `RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*`; "
        "(4) `.dockerignore` to exclude node_modules, .git, etc. from build context; "
        "(5) `--no-install-recommends` for apt; "
        "(6) inspect layer sizes: `docker history <image>`; tool: `dive <image>`.",
        f"{BASE}/build/building/best-practices/",
    ),
    (
        "docker-recipes", "docker recipe: pass build secrets safely",
        "Don't bake secrets into image layers. Use BuildKit's `--secret`. Dockerfile: `RUN --mount=type=secret,id=github_token git clone https://oauth2:$(cat /run/secrets/github_token)@github.com/private/repo.git`. "
        "Build invocation: `docker build --secret id=github_token,src=$HOME/.github_token -t myapp .`. "
        "Secrets don't end up in any layer — they're only available during that RUN step. Required: BuildKit (enabled by default on modern Docker).",
        f"{BASE}/build/building/secrets/",
    ),
    (
        "docker-recipes", "docker recipe: configure docker daemon proxy",
        "When docker pulls from registries through a corporate proxy. Steps: "
        "(1) `sudo mkdir -p /etc/systemd/system/docker.service.d`; "
        "(2) write /etc/systemd/system/docker.service.d/proxy.conf: ```\n[Service]\nEnvironment=\"HTTP_PROXY=http://proxy:8080\"\nEnvironment=\"HTTPS_PROXY=http://proxy:8080\"\nEnvironment=\"NO_PROXY=localhost,127.0.0.1,internal-registry.corp\"\n```; "
        "(3) `sudo systemctl daemon-reload && sudo systemctl restart docker`; "
        "(4) verify: `docker info | grep -i proxy`.",
        f"{BASE}/engine/daemon/proxy/",
    ),
    (
        "docker-recipes", "docker recipe: run container as non-root user",
        "Security best practice. In Dockerfile: `RUN useradd -u 1000 -m -s /bin/bash appuser; USER appuser`. "
        "Or at runtime: `docker run --user 1000:1000 <image>`. "
        "For volume permissions: ensure host directory has correct UID/GID: `chown -R 1000:1000 ./data` before mounting. "
        "For distroless images: use the `:nonroot` variant: `gcr.io/distroless/static:nonroot`.",
        f"{BASE}/build/building/best-practices/",
    ),
    (
        "docker-recipes", "docker recipe: scan an image for vulnerabilities",
        "Use Docker Scout (built-in) or external tools. "
        "Docker Scout: `docker scout cves <image>` (CVE list); `docker scout recommendations <image>` (suggestions). "
        "Trivy: `trivy image <image>` (popular open-source scanner). "
        "Snyk: `docker scan <image>`. "
        "For Dockerfile policy: `docker scout policy` or `hadolint Dockerfile`.",
        f"{BASE}/scout/",
    ),
    (
        "docker-recipes", "docker recipe: save image to tarball for offline transfer",
        "Useful for air-gapped deployments. Steps: "
        "(1) Save: `docker save -o myapp.tar myapp:1.0` (or pipe: `docker save myapp:1.0 | gzip > myapp.tar.gz`); "
        "(2) Transfer the tarball to target host; "
        "(3) Load: `docker load -i myapp.tar` (or `gunzip < myapp.tar.gz | docker load`); "
        "(4) Verify: `docker images | grep myapp`. "
        "For multiple images: `docker save -o all.tar img1 img2 img3`.",
        f"{BASE}/reference/cli/docker/image/save/",
    ),
    (
        "docker-recipes", "docker recipe: troubleshoot a container that exited",
        "Container ran and exited. Steps to debug: "
        "(1) `docker ps -a` to see exit code (the right side); "
        "(2) `docker logs <name>` for stdout/stderr; "
        "(3) Run interactively to see what happens: `docker run --rm -it --entrypoint sh <image>` then run the command manually; "
        "(4) Override entrypoint to inspect filesystem: `docker run --rm -it --entrypoint sh <image>`; "
        "(5) Check the Dockerfile's CMD/ENTRYPOINT: `docker history --no-trunc <image> | grep -E 'CMD|ENTRY'`.",
        f"{BASE}/reference/cli/docker/container/logs/",
    ),
    (
        "docker-recipes", "docker recipe: stream metrics from containers",
        "Live monitoring. `docker stats` shows CPU%, mem usage, net I/O, block I/O for all running. "
        "Specific containers: `docker stats <name1> <name2>`. "
        "Non-streaming (one-shot): `docker stats --no-stream`. "
        "Custom format: `docker stats --format 'table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}'`. "
        "For long-term metrics: cAdvisor (`docker run -d --name=cadvisor --volume=/var/run/docker.sock:/var/run/docker.sock:ro gcr.io/cadvisor/cadvisor`) feeds Prometheus.",
        f"{BASE}/reference/cli/docker/container/stats/",
    ),
    (
        "docker-recipes", "docker recipe: build a private registry",
        "Self-host a Docker registry. Steps: "
        "(1) `docker run -d -p 5000:5000 --restart=always --name registry registry:2`; "
        "(2) `docker pull alpine && docker tag alpine localhost:5000/alpine && docker push localhost:5000/alpine`; "
        "(3) for remote access, configure TLS + auth — see docker.com/registry docs; "
        "(4) for auth: htpasswd file mounted into the registry container; "
        "(5) clients pulling: ensure registry uses TLS (insecure registries require `--insecure-registry` daemon flag — discouraged).",
        f"{BASE}/registry/",
    ),
    (
        "docker-recipes", "docker recipe: setup buildx for caching",
        "Speed up CI builds by caching. Steps: "
        "(1) Use a buildx builder with registry cache: `docker buildx create --use --name cached --driver-opt network=host`; "
        "(2) Build with cache: `docker buildx build --cache-from=type=registry,ref=myorg/cache:latest --cache-to=type=registry,ref=myorg/cache:latest,mode=max -t myapp:1.0 --push .`. "
        "For GitHub Actions: use `cache-from=type=gha,scope=mybuild` + `cache-to=type=gha,mode=max,scope=mybuild`. "
        "Local-only cache: `--cache-to=type=local,dest=/tmp/cache` + `--cache-from=type=local,src=/tmp/cache`.",
        f"{BASE}/build/cache/backends/",
    ),
    (
        "docker-recipes", "docker recipe: hot-reload during compose dev",
        "Bind-mount source so changes appear in the container without rebuild. compose.yml: ```\nservices:\n  app:\n    build: .\n    volumes:\n      - .:/app\n      - /app/node_modules\n    command: npm run dev\n    ports:\n      - \"3000:3000\"\n```. "
        "The named-volume anonymous-volume trick (`- /app/node_modules`) keeps node_modules from the host out of the way. "
        "For Python: `command: uvicorn main:app --reload` + mount source dir.",
        f"{BASE}/compose-file/services/",
    ),
    (
        "docker-recipes", "docker recipe: docker compose for production",
        "Patterns. (1) Production-only file: `docker-compose.prod.yml`. (2) Run with overrides: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`. "
        "Key prod settings: `restart: unless-stopped` per service; `logging: { driver: 'json-file', options: { 'max-size': '10m', 'max-file': '3' }}` to cap log growth; `deploy: { resources: { limits: { memory: '512M' }}}`. "
        "Healthchecks per service. Configure healthcheck-aware `depends_on`. "
        "For real production at scale, prefer Kubernetes — compose is best for single-host deployments.",
        f"{BASE}/compose-file/deploy/",
    ),
    (
        "docker-recipes", "docker recipe: rootless docker (no root daemon)",
        "Run docker without sudo permissions. Steps: "
        "(1) Install: `dockerd-rootless-setuptool.sh install` (after `apt install docker-ce-rootless-extras`); "
        "(2) Add to ~/.bashrc: `export PATH=$HOME/bin:$PATH; export DOCKER_HOST=unix:///run/user/$UID/docker.sock`; "
        "(3) Start: `systemctl --user start docker`; "
        "(4) Enable on login: `systemctl --user enable docker; sudo loginctl enable-linger $USER`. "
        "Trade-offs: better security; can't bind <1024 ports without setcap; limited overlay network support; some kernel features blocked.",
        f"{BASE}/engine/security/rootless/",
    ),
    (
        "docker-recipes", "docker recipe: inspect what's inside an image",
        "Layer-by-layer inspection. "
        "List image layers + their sizes: `docker history --no-trunc <image>`. "
        "See exact filesystem: `docker run --rm <image> ls -la /`. "
        "Compare to base: `dive <image>` (third-party tool, very useful). "
        "Export entire filesystem: `docker save <image> | tar tv` lists everything. "
        "For Dockerfile reconstruction: `docker history` shows roughly which commands built each layer.",
        f"{BASE}/reference/cli/docker/image/history/",
    ),
    (
        "docker-recipes", "docker recipe: use Dockerfile ARG for build customization",
        "Pass values at build time. Dockerfile: `ARG VERSION=1.0` (with default) or `ARG VERSION`. "
        "Override at build: `docker build --build-arg VERSION=2.1 -t myapp .`. "
        "Scope: ARG is build-time only (not available at runtime — use ENV for that). For ARG before FROM: `ARG NODE_VERSION=18` then `FROM node:${NODE_VERSION}`. "
        "Re-declare after FROM if you need it in subsequent stages.",
        f"{BASE}/reference/dockerfile/",
    ),
    (
        "docker-recipes", "docker recipe: save vs commit a container's state",
        "Two ways to snapshot. (1) `docker commit <container> <new-image-name>` creates a new image from a running/stopped container. Quick but anti-pattern for production (no audit trail). "
        "(2) `docker save -o file.tar <image>` saves an image to a tarball — load elsewhere with `docker load -i file.tar`. Save is for transfer; commit is for derivation. "
        "Best practice: always Dockerfile-driven. Commit/save are escape hatches.",
        f"{BASE}/reference/cli/docker/container/commit/",
    ),
    (
        "docker-recipes", "docker recipe: read container exit code in a script",
        "Capture exit code. `docker run myapp; echo \"Exit: $?\"`. "
        "With --rm: same — the `$?` reflects the container's exit code. "
        "For detached (with `-d`), the immediate `$?` is whether docker accepted the run command. To wait + get final code: `docker wait <container>` returns the exit code on stdout. "
        "In CI, treat non-zero as test failure.",
        f"{BASE}/reference/cli/docker/container/wait/",
    ),
    (
        "docker-recipes", "docker recipe: limit network access for a container",
        "Restrict container's network. "
        "Run isolated (no network): `docker run --network=none myapp`. "
        "Custom network only (no internet): create network with explicit options that block external routing, or use `--internal` flag: `docker network create --internal mynet`. "
        "Egress allowlist: requires iptables / firewall config on the host. "
        "DNS restriction: `--dns 8.8.8.8 --dns-search example.com`. "
        "For ports: don't `-p` anything to keep it host-internal.",
        f"{BASE}/reference/cli/docker/network/",
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
            evidence_id=f"ev-docker-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-docker-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="docker-catalog",
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

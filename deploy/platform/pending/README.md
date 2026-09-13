# Pending Argo activation

The patched Argo image passed CI build, donor verification, both vulnerability gates, publication and keyless signing in run 34765117259. It is an EPS-derived image, not a vendor-signed release.

`python -m scripts.rehearse_argocd` installs only into `eps-v04-dev`. Preload the exact Argo and Redis digests first. The helper uses `imagePullPolicy: Never`, private services, restricted networking and a generated Redis password. It checks workload rollout, Redis authentication and denied EPS Secret/RBAC reads. It retains the installation and performs no application sync.

ApplicationSet requires `replicas: 0`; this chart ignores `enabled: false`. Redis requires `redis-server` as the first argument because its hardened image uses tini. Cloud installation, synchronization, failed-migration blocking and rollback remain open. Repository promotion remains operator-authored.

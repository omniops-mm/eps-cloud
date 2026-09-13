# Recovery and maintenance

Local checks reuse the existing helpers rather than a second restore manifest:

- `python -m scripts.check_live_observability --restart` replaces local Loki, Tempo, Prometheus and database pods, checks unchanged PVC identities, retrieves pre-restart telemetry and a database canary, then removes the canary.
- `python -m scripts.rehearse_kubernetes_db --restore-source` streams a custom PostgreSQL archive through host memory into a separate, network-isolated namespace/PVC. It verifies the migration version, rotates the disposable application's password, rejects the old password and checks a subsequent pod replacement. The target namespace, credentials and PVC are deleted. This is a restore test, not a retained off-site backup.
- `python -m scripts.check_live_observability --maintenance` rotates only the owned local test signing key, restarts web, proves old signed state fails and new state works, then explicitly runs both catch-up Jobs. Active jobs and externally managed credentials block the drill. Scheduled jobs stay suspended. Successful weather-job completion is not proof that every external data source returned data.

## Deployment password rotation

`rotate-db.yaml` is a manual Job, excluded from the application chart and GitOps. Never apply it with active writers. First prove a recoverable backup; suspend CronJobs, pause automation/HPA and stop web writers. Retain the previous credentials privately. Update the private application secret with a matching POSTGRES_PASSWORD and DATABASE_URL, wait for exact synchronization, then run the Job. Only the application role changes; administrator/exporter credentials and the signing key remain unchanged.

Check Job completion without printing its environment or Secret payloads. Verify new credentials succeed and old ones fail before restarting writers. Bootstrap environment variables alone do not rotate an existing role. If the Job fails, leave writers stopped and determine which credential pair is valid. Rollback requires restoring the previous Secret and running the role-change Job with that value before resuming writers. Never discard the old credentials before the new pair is proven.

For signing-key rotation, update only SECRET_KEY through the private secret source and restart every web replica. Existing signed sessions/forms become invalid. Confirm old signed state is rejected and newly issued state works. Restore normal scheduling/scaling only after the complete maintenance check.

Cloud backup retention, restore from an actual off-VM backup, Google secret rotation and Argo failed-migration/rollback evidence remain deployment acceptance gates. Local pod replacement does not prove node-loss recovery.

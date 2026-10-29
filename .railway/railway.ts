// Railway infrastructure for PunkRaven HR (replaces the deprecated Config as
// Code files). Variables stay in the Railway dashboard (managed: false), so no
// secret lives here. See docker/README.md, "Deploying on Railway".
import { defineRailway, github, image, postgres, project, redis, service, volume } from "railway/iac";

export default defineRailway(() => {
  const horillaHr = github("punk-raven/horilla-hr", { branch: "2.0", checkSuites: false });

  const Postgres = postgres("Postgres", { region: "asia-southeast1-eqsg3a" });
  Postgres.networking = { privateNetworkEndpoint: "postgres" };
  const Redis = redis("Redis", { region: "asia-southeast1-eqsg3a" });
  Redis.deploy = { startCommand: "/bin/sh -c \"rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/ && exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD --save 60 1 --dir $RAILWAY_VOLUME_MOUNT_PATH\"" };
  Redis.networking = { privateNetworkEndpoint: "redis" };
  const postgresVolumeKQev = volume("postgres-volume-kQev", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "asia-southeast1-eqsg3a", sizeMB: 5000 });
  const redisVolume2TxT = volume("redis-volume-2TxT", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "asia-southeast1-eqsg3a", sizeMB: 5000 });

  // No start command: on Railway it would replace the image ENTRYPOINT, and web
  // needs docker/entrypoint.sh to wait for Postgres, migrate and collectstatic
  // before gunicorn starts. A first boot applies every migration, hence 300s.
  const web = service("web", {
    source: horillaHr,
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/health/",
    healthcheckTimeout: 300,
    replicas: { "asia-southeast1-eqsg3a": 1 },
    deploy: { restartPolicyMaxRetries: 5 },
  });

  // Runs the APScheduler jobs. It binds no port, so it has no health check.
  // The jobs are not idempotent (payslips, backups): keep exactly one replica.
  // Needs HORILLA_SKIP_RELEASE_TASKS=1 so it never races web's migrate.
  const scheduler = service("scheduler", {
    source: horillaHr,
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "/entrypoint.sh python manage.py run_scheduler",
    replicas: { "asia-southeast1-eqsg3a": 1 },
    deploy: { restartPolicyType: "ALWAYS" },
  });

  const cloudflared = service("cloudflared", {
    source: image("cloudflare/cloudflared:2026.10.5"),
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    start: "cloudflared tunnel --no-autoupdate run",
    replicas: { "asia-southeast1-eqsg3a": 1 },
    deploy: { restartPolicyType: "ALWAYS" },
  });

  return project("PunkRaven-HR", {
    variables: { managed: false },
    resources: [Postgres, web, scheduler, Redis, cloudflared, postgresVolumeKQev, redisVolume2TxT],
  });
});

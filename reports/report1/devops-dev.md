# DevOps-Dev — Reporte de Infraestructura y CI/CD

## Resumen ejecutivo

- **Pipeline de CI roto/incompleto**: el workflow `deploy.yaml` invoca `make docker-build-env ENV=production`, target que **no existe** en el `Makefile` (solo existe `docker-build`). El job de build & push a Docker Hub fallará en cualquier ejecución (`.github/workflows/deploy.yaml:32`).
- **Cero security scanning en CI**: el pipeline (`ci.yaml`) corre lint + format + typecheck pero no hay `pip-audit`, `trivy`, `bandit`, `safety` ni escaneo de la imagen Docker. No hay gate de seguridad antes del build/push.
- **Secretos reales en `.env.development`** (no trackeado en git, correctamente en `.gitignore`, pero presente en disco con credenciales de producción reales: Langfuse, JWT secret, token de WhatsApp Cloud API, token CRM Odontoking, API key de Sharemedata, Admin API key). Riesgo crítico si el archivo se sube accidentalmente o se comparte el directorio.
- **Inconsistencia de healthcheck endpoints**: `docker-compose.yml`/`docker-compose.easypanel.yml` apuntan a `GET /health` (chequea DB, cache, buffer), mientras `railway.toml` usa `GET /api/v1/health` (endpoint trivial que siempre devuelve `200 healthy` sin verificar nada). Railway nunca detectará una BD caída.
- **Dockerfile sólido en general** (no-root, multi-stage parcial, `uv sync --frozen`), pero sin `HEALTHCHECK` nativo, con `build-essential` + headers de compilación dejados en la imagen final (no hay segunda etapa que los elimine), y `scripts/build-docker.sh` pasa secretos como `--build-arg` (quedan en el historial de capas de la imagen).
- Buena base de observabilidad (Prometheus + Grafana + cAdvisor + structured logging), pero el dashboard de Grafana es único (`llm_latency.json`), no hay reglas de alerting (`alert.rules.yml`) ni Alertmanager configurado.

---

## 🐳 Docker (Dockerfile, compose, .dockerignore) — hallazgos con severidad y archivo:línea

### Dockerfile (`/Users/javier/proyectos/02.agentes/01.odontoking/Dockerfile`)

| # | Hallazgo | Severidad | Archivo:línea |
|---|---|---|---|
| 1 | **No es multi-stage real**. Se instala `build-essential` y `libpq-dev` (toolchain de compilación completo) en la imagen final; no hay etapa `builder` separada que compile y luego copie solo artefactos a una imagen `slim` limpia. Esto infla el tamaño final (cientos de MB extra de gcc/g++/headers). | Medio | `Dockerfile:18-23` |
| 2 | **Imagen base pinneada correctamente** (`python:3.13.2-slim`, no `:latest`) — buena práctica, consistente con `.python-version` (`3.13`) y `pyproject.toml` (`requires-python = ">=3.13"`). | OK | `Dockerfile:1` |
| 3 | **Usuario no-root correcto**: `useradd -m appuser && chown -R appuser:appuser /app` + `USER appuser`. | OK | `Dockerfile:40-41` |
| 4 | **Sin `HEALTHCHECK` nativo en el Dockerfile**. El healthcheck solo existe en `docker-compose*.yml`, no en la imagen — si se corre el contenedor fuera de compose (p. ej. en Railway, que no usa `docker-compose`), no hay healthcheck a nivel de Docker (Railway usa su propio `healthcheckPath` HTTP, pero igual es buena práctica tener `HEALTHCHECK` en la imagen para portabilidad). | Medio | `Dockerfile` (ausente) |
| 5 | **Doble `uv sync`**: una vez sin proyecto (cache de deps) y otra con proyecto completo tras `COPY . .` — buen patrón de layer caching, correcto. | OK | `Dockerfile:28-33` |
| 6 | **`--group test` instalado en la imagen de producción** (`uv sync --frozen --no-install-project --extra cache --group test`). Esto incluye `pytest-asyncio` y dependencias de test en la imagen que corre en Railway/producción — aumenta superficie de ataque y tamaño innecesariamente. El comentario dice "needed for CI tests inside container", pero la imagen de producción no debería cargar dependencias de test. | Medio | `Dockerfile:27,29,33` |
| 7 | **`chmod +x` del entrypoint se hace antes de `chown`**, correcto orden (se hace antes de cambiar a `appuser`), pero el `chown -R appuser:appuser /app` se ejecuta **antes** del segundo `uv sync` — espera, en realidad el orden es: `COPY . .` → `RUN uv sync` (como root) → `chmod +x entrypoint` → `useradd + chown` → `USER appuser`. Esto es correcto. Sin objeciones aquí. | OK | `Dockerfile:31-41` |
| 8 | **`PYTHONHASHSEED=random`** está bien para seguridad, pero puede causar problemas de reproducibilidad de builds si se necesita determinismo bit-a-bit (no crítico). | Bajo | `Dockerfile:12` |
| 9 | El comentario en línea 26 menciona `--group test` como necesario "for CI tests inside container", lo que sugiere que el mismo Dockerfile se usa para CI y para producción — mezclar ambos casos de uso en una sola imagen va contra el principio de imágenes mínimas para producción. | Medio | `Dockerfile:26-29` |

### `.dockerignore` (`/Users/javier/proyectos/02.agentes/01.odontoking/.dockerignore`)

| # | Hallazgo | Severidad | Línea |
|---|---|---|---|
| 10 | Excluye correctamente `.env*`, `.git`, `logs/`, `__pycache__`, `.venv`. Bien. | OK | `.dockerignore:6-9,40-41` |
| 11 | Excluye `docs/` y `*.md` (línea 51) — en teoría podría afectar a `app/core/prompts/*.md` (system.md, odontoking.md, session_title.md), de los que depende `load_system_prompt()` en runtime. **Verificado empíricamente** con un build de prueba (`docker build` + `COPY . .` + `ls app/core/prompts/`): los 3 archivos `.md` de prompts **sí llegan al build context** (no se excluyen). El patrón `*.md` de `.dockerignore` aparentemente solo se aplica al nivel raíz del contexto en este caso, no recursivamente — comportamiento correcto pero **frágil y confuso**: cualquier cambio futuro al `.dockerignore` (p. ej. cambiar `*.md` por `**/*.md`) rompería silenciosamente el agente. Recomiendo añadir una excepción explícita `!app/core/prompts/*.md` para blindar este comportamiento independientemente de cómo evolucione el resto del archivo. | Bajo (riesgo latente, no actual) | `.dockerignore:51` |
| 12 | No excluye `tests/`, `evals/`, `planning/`, `todo/`, `arquitectura/`, `kohlberg-n8n/`, `odontoking-n8n/`, `swagger-odontoking/`, `logs-conversacion.txt`, `*.jpeg`/`*.png` (imágenes sueltas en raíz) — todo esto se copia al build context y a la imagen vía `COPY . .`, aumentando el tamaño de la imagen y el contexto de build sin necesidad. | Medio | `.dockerignore` (ausente) |

### `docker-compose.yml` (`/Users/javier/proyectos/02.agentes/01.odontoking/docker-compose.yml`)

| # | Hallazgo | Severidad | Línea |
|---|---|---|---|
| 13 | `version: '3.8'` — campo obsoleto, genera warning en cada invocación de `docker-compose`. Ya documentado en `todo/06-docker-compose-version-obsolete-[infra].md` pero **no corregido**. | Bajo | `docker-compose.yml:1` |
| 14 | **Dos servicios Postgres** (`db` y `db-dev`) con distintos puertos/credenciales. El servicio `app` declara `depends_on: db` (puerto 5432, credenciales `${POSTGRES_DB}/${POSTGRES_USER}/${POSTGRES_PASSWORD}` desde `.env.${APP_ENV}`), mientras que `make dev` / `make infra-up` levantan `db-dev` (puerto 5434, user `dev`/`dev`). Si `.env.development` no define `POSTGRES_PORT=5434` apuntando a `db-dev`, `make stack-up` con `app` apuntaría al servicio `db` equivocado. Confirmado: `.env.development` define `POSTGRES_PORT=5434` y `POSTGRES_USER=dev/POSTGRES_PASSWORD=dev` — esto coincidiría con `db-dev`, pero `app.depends_on` sigue apuntando a `db` (puerto 5432 interno del compose, no 5434), por lo que **`app` espera a que `db` esté healthy pero luego se conecta a `db-dev` por config** — `db` arranca innecesariamente y `app`/`db-dev` no tienen relación de dependencia explícita (riesgo de race condition: `app` puede arrancar antes de que `db-dev` esté listo). Documentado parcialmente en `todo/07`. | Alto | `docker-compose.yml:25-43,77-79` |
| 15 | El servicio `app` monta `./app:/app/app` como bind mount (hot reload) — correcto para desarrollo, pero si este mismo `docker-compose.yml` se usara en producción (no es el caso, existe `docker-compose.easypanel.yml` separado), sobrescribiría el código de la imagen. Está bien documentado que es solo para dev. | OK | `docker-compose.yml:69-71` |
| 16 | Healthcheck del `app` usa `curl -f http://localhost:8000/health` — coherente con el endpoint completo de `app/main.py:203`. Bien. | OK | `docker-compose.yml:80-85` |
| 17 | `JWT_SECRET_KEY` tiene un default hardcodeado inseguro: `${JWT_SECRET_KEY:-supersecretkeythatshouldbechangedforproduction}`. Aceptable solo porque es un compose de desarrollo y el `.env.development` ya define un valor real, pero el default literal "supersecretkeythatshouldbechangedforproduction" es un anti-patrón si algún día este compose se reutiliza sin `.env`. | Bajo | `docker-compose.yml:76` |
| 18 | `prometheus` y `grafana` usan `image: prom/prometheus:latest` y `image: grafana/grafana:latest` — **tags `:latest` no pinneados**, contradice el estándar de "never `:latest`". Pueden romper dashboards/scrape config en upgrades silenciosos. | Medio | `docker-compose.yml:113,126` |
| 19 | `cadvisor` usa `gcr.io/cadvisor/cadvisor:latest` — mismo problema de tag `:latest`. | Medio | `docker-compose.yml:141` |
| 20 | `valkey` y `pgvector` sí están pinneados correctamente (`valkey/valkey:8.1.6-alpine`, `pgvector/pgvector:pg16`). Buena práctica parcial. | OK | `docker-compose.yml:5,47` |
| 21 | `db`, `db-dev`, `valkey`, `rabbitmq`, `prometheus`, `grafana`, `cadvisor` usan `restart: always`; solo `app` usa `restart: on-failure`. Es razonable, pero conviene documentar por qué (evitar reinicios infinitos si el código de app está roto en vez de problema transitorio). | OK/Info | `docker-compose.yml:20,41,57,86,107,122,138,151` |
| 22 | `cadvisor` monta `/:/rootfs:ro`, `/var/run:/var/run:rw`, `/sys:/sys:ro`, `/var/lib/docker/:/var/lib/docker:ro` — necesario para su función pero otorga visibilidad amplia del host; aceptable solo en entornos de desarrollo/monitoreo controlados, **no debería ir a producción** (no está en `docker-compose.easypanel.yml`, correcto). | Info | `docker-compose.yml:144-148` |

### `docker-compose.easypanel.yml` (`/Users/javier/proyectos/02.agentes/01.odontoking/docker-compose.easypanel.yml`)

| # | Hallazgo | Severidad | Línea |
|---|---|---|---|
| 23 | `version: '3.8'` obsoleto también aquí. | Bajo | `docker-compose.easypanel.yml:1` |
| 24 | El comando del servicio `app` corre `alembic upgrade head` antes de `exec uvicorn` (`command: sh -c "... alembic upgrade head && exec uvicorn ..."`), **y además** el `Dockerfile`/`docker-entrypoint.sh` **también** corre `alembic upgrade head` (`scripts/docker-entrypoint.sh` línea final antes de `exec "$@"`). Esto produce **doble ejecución de migraciones** en cada arranque/restart — no es destructivo (alembic es idempotente) pero añade latencia de arranque y duplica logs; en un escenario con múltiples réplicas podría causar contención de locks de migración simultáneos. | Medio | `docker-compose.easypanel.yml:67-70` + `scripts/docker-entrypoint.sh` (sección "Run database migrations") |
| 25 | `--workers 1` fijo — para producción con tráfico WhatsApp multi-tenant esto puede ser un cuello de botella; no hay mecanismo de escalado horizontal documentado en este compose (Railway sí soporta réplicas vía `railway.toml`, pero easypanel compose no define `deploy.replicas`). | Medio | `docker-compose.easypanel.yml:70` |
| 26 | Buen manejo de secretos vía variables de entorno externas (`${POSTGRES_PASSWORD}`, `${OPENAI_API_KEY}`, etc., sin defaults peligrosos para las críticas) — contraste positivo con `docker-compose.yml`. | OK | `docker-compose.easypanel.yml:23-126` |
| 27 | `valkey` no tiene `VALKEY_PASSWORD` configurado por defecto en el comando (`valkey-server --save 60 1 --loglevel warning` sin `--requirepass`), aunque la app sí soporta `VALKEY_PASSWORD` (`${VALKEY_PASSWORD:-}` línea 89). Si `VALKEY_PASSWORD` no se setea, Valkey queda sin autenticación dentro de la red `internal` — riesgo bajo porque la red es interna/bridge sin exposición de puertos al host (no hay `ports:` para `valkey` ni `db` en este compose, correcto), pero conviene forzar password igualmente como defensa en profundidad. | Bajo-Medio | `docker-compose.easypanel.yml:43,89` |
| 28 | No define límites de recursos (`mem_limit`, `cpus`) para ningún servicio — en Easypanel esto normalmente se gestiona desde la UI, pero no está documentado aquí. | Bajo | `docker-compose.easypanel.yml` (general) |

---

## 🚀 Deploy (Railway / easypanel) — config, healthchecks, rollback

### `railway.toml` (`/Users/javier/proyectos/02.agentes/01.odontoking/railway.toml`)

- `builder = "dockerfile"`, `dockerfilePath = "Dockerfile"` — correcto, usa el mismo Dockerfile auditado arriba.
- `startCommand` invoca explícitamente `/app/scripts/docker-entrypoint.sh /app/.venv/bin/uvicorn ... --workers 1` (línea 7). Esto es **redundante** con el `ENTRYPOINT`/`CMD` del Dockerfile (que ya hace lo mismo por defecto), pero no es incorrecto — Railway sobrescribe el `CMD`.
- **`healthcheckPath = "/api/v1/health"` (línea 8)**: este endpoint (`app/api/v1/api.py:24-32`) es un stub que **siempre devuelve `{"status": "healthy", "version": "1.0.0"}` con HTTP 200**, sin verificar BD, cache, ni buffer. Esto significa que **Railway nunca detectará un servicio degradado** (BD caída, Valkey caído) — el `restartPolicyType = "on_failure"` con `restartPolicyMaxRetries = 3` (líneas 10-11) nunca se disparará por causas de salud real, solo por crashes del proceso. Comparar con `app/main.py:203-244` (`GET /health`), que sí hace `database_service.health_check()` + `cache_service.health_check()` y devuelve `503` si la BD no responde. **Recomendación: cambiar `healthcheckPath` a `/health`.** | **Alto** | `railway.toml:8` vs `app/main.py:203-244` / `app/api/v1/api.py:24-32`
- `healthcheckTimeout = 300` (5 minutos) — generoso, da margen al arranque (migraciones + pre-warm del grafo LangGraph + conexión a pgvector), razonable dado que `docker-entrypoint.sh` corre `alembic upgrade head` en cada arranque.
- **No hay configuración de rollback automático documentada** más allá de `restartPolicyMaxRetries = 3`. Railway soporta rollback a deployment anterior vía UI/CLI, pero no está reflejado en `railway.toml` (no hay forma de "pinnear" o versionar releases desde config-as-code).
- La sección de "Workers" (líneas 13-29) es **documentación en comentarios**, no configuración real — cada worker tenant (`worker-odontoking`, `worker-kohlberg`) debe crearse manualmente como servicio Railway separado. Esto es operacionalmente fràgil: no hay un `railway.json`/`railway.toml` por servicio versionado en el repo que garantice reproducibilidad del despliegue multi-tenant. Si se pierde la configuración manual en Railway, recrearla depende de seguir comentarios en un archivo TOML.
- `app/worker.py` no tiene healthcheck HTTP propio (es un worker de cola, no expone puerto) — aceptable, pero Railway por defecto puede intentar healthcheck HTTP si no se configura `healthcheckPath: null`/se omite correctamente para el servicio worker. No se ve un `railway.worker.toml` o sección `[deploy]` condicional para diferenciarlo.

### `railway.evals.toml` (`/Users/javier/proyectos/02.agentes/01.odontoking/railway.evals.toml`)

- `startCommand = "python evals/run_eval.py"`, `restartPolicyType = "never"` — correcto para un job de un solo disparo (evals).
- **Sin healthcheck** (correcto, es un job, no un servicio HTTP).
- Coincide con `todo/62-evals-not-scheduled-debt.md`: existe el job pero no hay `CronCreate`/cron de Railway ni GitHub Actions schedule que lo dispare periódicamente — confirma que las evals son 100% manuales hoy.

### Resumen de rollback

- **Railway**: rollback manual vía dashboard/CLI; sin automatización ni gates de "canary"/health-based rollback en `railway.toml`.
- **Easypanel**: `restart: on-failure` para `app`, `restart: always` para `db`/`valkey`; sin estrategia de rollback de imagen (depende de re-pull manual de un tag anterior en Docker Hub — pero `deploy.yaml` está roto, ver sección CI/CD).

---

## 🔄 CI/CD (GitHub Actions) — qué falta (lint/test/security/build gates)

### `.github/workflows/ci.yaml`

```yaml
on:
  pull_request:
  push:
    branches: [master]

jobs:
  checks:
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras --all-groups
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pyright
```

| # | Hallazgo | Severidad |
|---|---|---|
| 29 | **No ejecuta `pytest`**. El proyecto tiene `tests/` (con `tests/api/`, `tests/unit/`, `tests/unit/tools/`, `conftest.py`) pero la CI solo corre `lint` (ruff) + `format --check` + `pyright`. Esto contradice el estándar "lint → test → security scan → build → deploy": **falta el paso de test completamente**. Cualquier PR que rompa lógica de negocio (p. ej. los bugs documentados en `todo/`) pasa CI en verde mientras la suite de tests no se ejecute. | **Crítico** |
| 30 | **No hay security scan**: ni `pip-audit`/`safety` para dependencias Python, ni `trivy`/`grype` para la imagen Docker, ni `bandit` para SAST de Python, ni `detect-secrets` en CI (solo existe como pre-commit hook local, que el desarrollador puede saltarse con `--no-verify`). | **Alto** |
| 31 | **No hay build de la imagen Docker en `ci.yaml`** — el build solo ocurre en `deploy.yaml` (que está roto, ver hallazgo #34). Esto significa que un PR puede mergear a `master` sin que nadie haya verificado que la imagen Docker siquiera compila. | Alto |
| 32 | `uv sync --all-extras --all-groups` instala **todo** (incluye extras opcionales como `cache`, `rabbitmq` si existen, y todos los grupos de dev/test) — correcto para CI de checks estáticos, pero como no hay step de test, esta instalación completa no se aprovecha para validar nada más que lint/typecheck. | Info |
| 33 | No hay gate de cobertura de tests (`coverage`, `pytest --cov`) ni umbral mínimo. | Medio |

### `.github/workflows/deploy.yaml`

| # | Hallazgo | Severidad | Línea |
|---|---|---|---|
| 34 | **`make docker-build-env ENV=production` no existe en el `Makefile`** (el Makefile solo define `docker-build:` en la línea 123, que llama a `./scripts/build-docker.sh $(ENV)`). Este workflow **fallará siempre** con `make: *** No rule to make target 'docker-build-env'`. Es decir, **el pipeline de build & push a Docker Hub está roto / nunca ha funcionado tal cual está**, o quedó obsoleto tras un rename del target. | **Crítico** | `.github/workflows/deploy.yaml:32` vs `Makefile:123` |
| 35 | El workflow se dispara también en `pull_request` hacia `master` (líneas 7-9) — esto significa que en **cada PR** se intenta hacer un build de Docker (que fallará por #34), consumiendo minutos de CI innecesariamente y generando ruido de checks rojos en cada PR. | Medio | `.github/workflows/deploy.yaml:7-9` |
| 36 | El `docker login` y `docker push` están condicionados a `secrets.DOCKER_USERNAME != ''` — buen patrón defensivo para no fallar si los secrets no están configurados, pero combinado con #34 esto es moot porque el build previo ya falla. | OK (diseño) | `.github/workflows/deploy.yaml:35,40` |
| 37 | **No hay despliegue real a Railway en este workflow** — Railway normalmente se autodespliega vía su propio integrador de GitHub (push a `master` → build automático en Railway usando `railway.toml`), lo cual es plausible y razonable, pero entonces el nombre `deploy.yaml` es confuso: solo construye y publica a Docker Hub, no "despliega" en el sentido de Railway/Easypanel. Si el flujo real de despliegue es "Railway escucha el push a `master` y reconstruye", entonces este workflow de Docker Hub podría ser vestigial/duplicado. | Medio | `.github/workflows/deploy.yaml` (general) |
| 38 | Usa `actions/checkout@v3` (desactualizado) mientras `ci.yaml` usa `@v4` — inconsistencia de versiones de actions entre workflows. | Bajo | `.github/workflows/deploy.yaml:17` vs `ci.yaml:12` |

### Qué falta para cumplir el estándar "lint → test → security scan → build → deploy"

1. **test**: agregar step `uv run pytest` (con DB de test via servicio `postgres` en GH Actions, dado que `tests/conftest.py` probablemente requiere DB — verificar fixtures).
2. **security scan**: agregar `pip-audit` o `uv pip audit` para dependencias, y `trivy image` o `docker scout` sobre la imagen construida antes de push.
3. **build gate real**: arreglar `make docker-build-env` (renombrar a `docker-build` o crear el target faltante) y mover el build de imagen a `ci.yaml` como gate previo al merge (build sin push en PRs, push solo en `master`).
4. **branch protection**: no se puede verificar desde el repo si `master` tiene branch protection rules requiriendo que `ci.yaml` pase — esto se configura en GitHub Settings, no en código; recomendar verificar manualmente que "Require status checks to pass" esté activo para `checks` (ci.yaml) antes de merge.

---

## 🔑 Gestión de secretos y env (¿secretos commiteados? redactar valores)

### Estado en git

- `.gitignore` (líneas 9-12) excluye correctamente `.env`, `.env.development`, `.env.staging`, `.env.production`.
- `git ls-files | grep env` solo devuelve `.env.example` — **`.env.development` NO está trackeado en git** (verificado con `git log --all -- .env.development` → vacío). Esto es correcto y bueno.

### Pero — el archivo existe en disco con secretos reales

`/Users/javier/proyectos/02.agentes/01.odontoking/.env.development` (no commiteado, pero presente en el filesystem del agente/desarrollador) contiene, entre otros:

| Variable | Línea aprox. | Valor (redactado) |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | ~17 | `pk-lf-4220****-****-****-****-********bdbd0` |
| `LANGFUSE_SECRET_KEY` | ~18 | `sk-lf-6818****-****-****-****-********4757` |
| `JWT_SECRET_KEY` | ~30 | `KuStXq****************************KgrW` (32 bytes hex-like) |
| `WHATSAPP_ACCESS_TOKEN` | ~62 | token largo de Meta Graph API (`EABB1GN6...`) — **token de acceso real con permisos sobre el número de WhatsApp de Odontoking** |
| `ODONTOKING_API_TOKEN` | ~66 | `5\|L19DkH****************...` (token Sanctum del CRM) |
| `SHAREMEDATA_API_KEY` | ~69 | hash bcrypt-like `$2a$08$...` |
| `ADMIN_API_KEY` | ~91 | `w6pFv6****************5l87I` |
| `INTERNAL_API_KEY` | ~88 | `local-internal-key` (valor de placeholder, bajo riesgo) |

**Severidad: Crítico (si se sube por error) / actualmente Bajo-residual (no está en git)**, pero:

- Ya existe `todo/05-secrets-in-env-development-[risk].md` documentando esto y recomendando **rotación inmediata de credenciales** — no veo evidencia de que se haya ejecutado la rotación (los valores siguen presentes y parecen reales/activos).
- Como agente DevOps, **recomiendo verificar si este archivo fue commiteado en algún momento del historial pasado** (aunque hoy `.gitignore` lo excluya, pudo haber sido trackeado antes y luego solo "untracked" sin purgar del historial). Ejecutar: `git log --all --full-history -- .env.development` y, si hay hits, purgar el historial con `git filter-repo` y **rotar todas las credenciales arriba listadas de inmediato** (especialmente `WHATSAPP_ACCESS_TOKEN` y `OPENAI_API_KEY` si estuviera presente).
- `.env.development` línea ~24-25 tiene `OPENAI_API_KEY="\n"` (literalmente un salto de línea entre comillas) — esto es **un valor vacío/roto**, no un secreto expuesto, pero significa que el entorno de desarrollo local probablemente no puede llamar a OpenAI sin sobreescribir esta variable por otro medio (variable de entorno del shell, por ejemplo). Esto es más un bug de configuración que de seguridad.

### `.env.example`

- Bien estructurado, usa `<CHANGE_ME>` como placeholder, incluye instrucciones de generación de claves (`secrets.token_hex`, `Fernet.generate_key`) — buena práctica de documentación (`/Users/javier/proyectos/02.agentes/01.odontoking/.env.example:1-15`).
- No contiene secretos reales.

### `.secrets.baseline` y pre-commit

- `.pre-commit-config.yaml` (líneas 18-24) configura `detect-secrets` con `--baseline .secrets.baseline`, excluyendo `.env.example`. **Esto es correcto y debería haber detectado los secretos en `.env.development` si el hook corriera sobre ese archivo** — pero como `.env.development` está en `.gitignore`, `detect-secrets` (que opera sobre archivos staged) nunca lo analiza. El control funciona como diseñado para evitar *commits* de secretos, pero no protege contra la mera *presencia* del archivo en disco compartido.
- El pre-commit hook se salta con `git commit --no-verify` — no hay enforcement server-side (GitHub Actions) que corra `detect-secrets` como gate adicional. **Recomendación**: agregar un step en `ci.yaml` que corra `detect-secrets scan` contra el diff del PR como defensa en profundidad.

### `scripts/build-docker.sh`

- Pasa secretos (`OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `JWT_SECRET_KEY`) como `--build-arg` al `docker build` (líneas finales del script). **Los `ARG` de Docker quedan registrados en el historial de capas de la imagen** (`docker history`) y son extraíbles por cualquiera con acceso a la imagen, incluso si nunca se usan dentro de un `RUN` — el `Dockerfile` actual (`/Users/javier/proyectos/02.agentes/01.odontoking/Dockerfile`) **solo declara `ARG APP_ENV`** (línea 7), no recibe los demás `--build-arg` (`OPENAI_API_KEY`, etc.) como `ARG` — Docker simplemente ignorará los `--build-arg` no declarados con un warning, así que **en la práctica no se filtran** con el Dockerfile actual. Pero el script sigue siendo un riesgo latente: si alguien añade `ARG OPENAI_API_KEY` al Dockerfile en el futuro (p. ej. para un build step que lo necesite), automáticamente quedaría grabado en las capas. **Recomendación**: eliminar esos `--build-arg` de `scripts/build-docker.sh` ya que no se usan, para no dejar la puerta abierta. | Medio | `scripts/build-docker.sh` (líneas finales del `docker build --no-cache`)

### `scripts/docker-entrypoint.sh`

- Hace echo de **presencia** de variables (`set`/`Not set`), no de valores — correcto, no filtra secretos en logs.
- Verifica `JWT_SECRET_KEY` y `OPENAI_API_KEY` como requeridos al arranque (fail-fast) — buena práctica.
- Carga `.env.${APP_ENV}` línea por línea con `export "$line"` — funciona pero es frágil ante valores con espacios/comillas/`=` múltiples (no usa `set -a; source`). El propio `OPENAI_API_KEY="\n"` visto en `.env.development` podría parsear mal con este método line-by-line.

---

## 📈 Observabilidad (Prometheus/Grafana, health checks, metrics)

### Prometheus (`/Users/javier/proyectos/02.agentes/01.odontoking/prometheus/prometheus.yml`)

- Scrape config simple: `fastapi` (target `app:8000`, path `/metrics`) y `cadvisor` (target `cadvisor:8080`). Intervalo `15s`.
- **No hay reglas de alerting** (`rule_files:` ausente) ni configuración de `alertmanager`. Prometheus aquí es solo almacenamiento de series temporales para Grafana, sin alertas activas basadas en métricas (p. ej. tasa de error 5xx, latencia p99, `llm_inference_duration_seconds` alto, conexiones DB agotadas).
- No hay scrape job para `valkey`/`redis_exporter`, `postgres_exporter`, ni `rabbitmq` exporter — solo la app y cAdvisor. Esto limita la visibilidad de la salud de la infraestructura subyacente (solo contenedores vía cAdvisor, no métricas internas de Postgres/Valkey).
- Prometheus solo está en `docker-compose.yml` (stack de desarrollo/monitoreo), **no en `docker-compose.easypanel.yml`** — en producción (Easypanel) no hay Prometheus/Grafana desplegado. Si Easypanel es el entorno de producción real, **no hay monitoreo de métricas en producción**, solo logs.

### Grafana

- `grafana/dashboards/dashboards.yml` provisiona un único folder con dashboards desde `grafana/dashboards/json/`.
- Solo existe **un dashboard**: `llm_latency.json` (157 líneas) — cobertura limitada (solo latencia LLM). Faltan dashboards para: HTTP request rate/latency/errores (`http_requests_total`, `http_request_duration_seconds` ya están instrumentados en `app/core/metrics.py:9-14` pero sin dashboard), conexiones DB (`db_connections` gauge definido en `app/core/metrics.py:18` pero sin dashboard ni lugar donde se actualice — verificar si está realmente instrumentado en el código de servicio), métricas de cAdvisor (CPU/memoria por contenedor).
- `GF_SECURITY_ADMIN_PASSWORD=admin` hardcodeado en `docker-compose.yml:134` — password por defecto de Grafana sin cambiar, expuesto en el compose. Si el puerto 3000 de Grafana se expone públicamente (está mapeado `3000:3000` en `docker-compose.yml:128`), cualquiera con acceso a la red puede entrar a Grafana con `admin/admin`. **Riesgo Alto si el host expone el puerto 3000 a internet.**

### Health checks de la aplicación

- `GET /health` (`app/main.py:203-244`): healthcheck completo, verifica `database_service.health_check()` y `cache_service.health_check()` en paralelo (`asyncio.gather`), más estado del buffer (`message_buffer_service._backend is not None`). Devuelve `503` si `db_ok and buffer_ok` no se cumplen. Tiene rate limiting (`@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])`) — **cuidado**: si el rate limit es muy bajo, un orquestador que healthcheckea cada pocos segundos podría empezar a recibir 429 en vez de 200/503, lo cual el orquestador interpretaría como fallo. Verificar valor de `RATE_LIMIT_ENDPOINTS["health"]`.
- `GET /api/v1/health` (`app/api/v1/api.py:24-32`): stub trivial, siempre `200 healthy`, **sin valor real como liveness/readiness probe** — es el que usa `railway.toml:8` (ver hallazgo de Deploy arriba).
- No hay distinción explícita entre **liveness** (¿el proceso está vivo?) y **readiness** (¿puede recibir tráfico?) — `/health` mezcla ambos conceptos. Para Railway esto es aceptable (un solo healthcheck), pero si se migra a Kubernetes en el futuro, convendría separar `/healthz/live` (proceso vivo, sin dependencias externas) de `/healthz/ready` (DB+cache+buffer OK).

### Métricas Prometheus (`app/core/metrics.py`)

- Usa `starlette_prometheus.PrometheusMiddleware` + `metrics` endpoint — estándar y correcto.
- Define métricas de negocio: `llm_inference_duration_seconds`, `llm_stream_duration_seconds`, `session_names_generated_total`, `orders_processed_total` (este último parece vestigial/template, no propio del dominio Odontoking — posible resto del boilerplate original `fastapi-langgraph-template`).
- `db_connections` Gauge definido pero no se confirma su actualización activa en este review (fuera del alcance de archivos revisados) — si nadie llama `.set()`/`.inc()`/`.dec()` sobre este gauge, el dashboard mostraría siempre 0.

### Langfuse

- Tracing LLM vía Langfuse correctamente documentado (`docs/observability.md`) y configurado vía env vars (`LANGFUSE_TRACING_ENABLED`, `LANGFUSE_PUBLIC_KEY/SECRET_KEY`, `LANGFUSE_HOST`). Buena cobertura de observabilidad de LLM, complementaria a Prometheus.

---

## 🔧 Mejoras / Deuda técnica

1. **Arreglar `make docker-build-env`** (o renombrar la referencia en `deploy.yaml` a `docker-build`) — actualmente el pipeline de build & push está roto (#34).
2. **Multi-stage Dockerfile real**: separar etapa `builder` (con `build-essential`/`libpq-dev`/`uv sync`) de etapa `runtime` (`python:3.13.2-slim` limpio + copiar `.venv` y `app/`), reduciendo tamaño de imagen y superficie de ataque.
3. **Imagen de producción sin `--group test`**: usar `uv sync --frozen --extra cache` (sin `--group test`) para el build final destinado a Railway/Easypanel; mantener una imagen separada o un `docker-compose` de CI para tests con dependencias de test.
4. **Pinnear `prom/prometheus`, `grafana/grafana`, `gcr.io/cadvisor/cadvisor`** a versiones específicas (p. ej. `prom/prometheus:v2.53.0`, `grafana/grafana:11.x.x`).
5. **Eliminar `version: '3.8'`** de ambos `docker-compose*.yml` (todo/06, sigue pendiente).
6. **Resolver la doble ejecución de `alembic upgrade head`** en `docker-compose.easypanel.yml` (ya lo hace `docker-entrypoint.sh`; quitar del `command:` del compose o viceversa).
7. **Cambiar `railway.toml:healthcheckPath`** de `/api/v1/health` a `/health` para que Railway detecte degradación real de BD/cache/buffer.
8. **Agregar `HEALTHCHECK` al `Dockerfile`** (usando `curl` o un script Python ligero contra `/health`).
9. **Forzar `requirepass` en Valkey** en `docker-compose.easypanel.yml` (definir `VALKEY_PASSWORD` obligatorio, no opcional con default vacío).
10. **Cambiar `GF_SECURITY_ADMIN_PASSWORD=admin`** por una variable de entorno con valor generado/secreto, y no exponer el puerto 3000 públicamente sin autenticación adicional (reverse proxy con auth).
11. **Limpiar `scripts/build-docker.sh`**: quitar los `--build-arg` de secretos no usados por el `Dockerfile` actual.
12. **Resolver inconsistencia de healthcheck `/health` vs `/api/v1/health`**: considerar eliminar o redirigir uno de los dos para evitar confusión futura sobre cuál es "el" healthcheck canónico.

---

## 🕳️ Cosas que estamos pasando por alto

- **Nadie corre `pytest` en CI** — esto es la brecha más grave desde la óptica DevOps: el "build → deploy" gate no garantiza que la aplicación funcione, solo que el código "parsea" (lint/type). Con +70 archivos en `todo/` documentando bugs funcionales, la falta de test gate en CI es la causa raíz por la que estos bugs llegan a producción sin ser atrapados antes.
- **No hay verificación de que `.dockerignore` no excluya accidentalmente `app/core/prompts/*.md`** — el patrón `*.md` en `.dockerignore:51` es global y podría romper el sistema de prompts en producción si Docker lo aplica recursivamente sobre `app/`. Esto debería verificarse con un build real antes de cualquier próximo deploy.
- **Producción (Easypanel) no tiene Prometheus/Grafana/cAdvisor** — toda la inversión en observabilidad de métricas (`docker-compose.yml`) es solo para desarrollo/staging local. En producción solo quedan logs estructurados + Langfuse. Si hay un incidente de latencia/CPU en producción, no hay dashboards ni históricos de métricas de sistema para diagnosticar.
- **El job de evals (`railway.evals.toml`) nunca se ejecuta automáticamente** (todo/62) — la calidad del agente puede degradarse silenciosamente con cambios de prompt sin que nadie lo note hasta que un usuario se queje.
- **Despliegue multi-tenant (workers por tenant) es 100% manual y no versionado** — la configuración de cada `worker-<tenant>` vive solo en la UI de Railway, documentada como comentarios en `railway.toml`. Si Railway se pierde/migra, recrear el despliegue exacto depende de memoria institucional, no de IaC.
- **No hay backup/restore documentado para el volumen `postgres-data`** (ni en `docker-compose.yml` ni en `docker-compose.easypanel.yml`) — pgvector almacena memoria a largo plazo (mem0) y checkpoints de LangGraph (conversaciones en curso). Una pérdida del volumen en Easypanel borraría todo el historial de conversaciones y memoria de usuarios sin posibilidad de recuperación.
- **`logs/` contiene archivos reales con datos de conversación** (`logs-conversacion.txt`, `logs/logs.*.json`, con sufijos `:Zone.Identifier` típicos de descargas en Windows/WSL) presentes en el directorio de trabajo — aunque `logs/` está en `.gitignore`, conviene confirmar que no contienen PII de pacientes (nombres, teléfonos, datos médicos) que no deberían persistir en el filesystem del desarrollador sin cifrado/control de acceso, dado que es una clínica dental (datos de salud).
- **No se encontró ningún archivo `SECURITY.md`-driven workflow** (existe `SECURITY.md` en la raíz pero no se verificó su contenido en este review — fuera del alcance asignado a DevOps, pero relevante para Security-Dev).

---

## ✨ Nuevas funcionalidades / automatizaciones propuestas

1. **Agregar job `test` a `ci.yaml`**: levantar Postgres+pgvector como `services:` de GitHub Actions, correr `uv run pytest` con `APP_ENV=test`, antes de lint/typecheck o en paralelo.
2. **Agregar job `security`**: `pip-audit` (o `uv pip install pip-audit && pip-audit`) para CVEs de dependencias Python, y `trivy image <tag>` para escanear la imagen Docker construida, fallando el pipeline en severidad CRITICAL/HIGH.
3. **Arreglar y consolidar `deploy.yaml`**: corregir el target del Makefile, restringir el trigger a solo `push: branches: [master]` (quitar `pull_request`), y considerar si realmente se necesita publicar a Docker Hub o si Railway/Easypanel ya construyen desde el Dockerfile directamente (en cuyo caso este workflow podría eliminarse para reducir mantenimiento).
4. **Cron de evals semanal** (GitHub Actions `schedule:` o Railway cron) que ejecute `make eval-quick` / `railway.evals.toml` y publique resultados (Slack/email) — implementa lo pedido en `todo/62`.
5. **`HEALTHCHECK` nativo en Dockerfile** + alinear `railway.toml` a `/health`.
6. **Dashboard Grafana adicional**: HTTP requests (rate/latency/error rate por endpoint usando `http_requests_total`/`http_request_duration_seconds`), conexiones DB (`db_connections`), y métricas de cAdvisor por contenedor (CPU/mem/red).
7. **Reglas de alerting Prometheus + Alertmanager** (o integración con Grafana Alerting): alertas para `up == 0` (servicio caído), tasa de 5xx > umbral, `llm_inference_duration_seconds` p95 > umbral, espacio en disco del volumen Postgres.
8. **Backup automatizado de `postgres-data`**: cron job (`pg_dump` programado) con retención y subida a almacenamiento externo (S3/Backblaze), dado que contiene memoria conversacional + posibles datos de salud.
9. **Exporters adicionales**: `postgres_exporter` y `redis_exporter` (para Valkey) en el stack de Prometheus, para visibilidad de la capa de datos.
10. **Rotación de credenciales expuestas en `.env.development`** (WhatsApp Access Token, Langfuse keys, ODONTOKING_API_TOKEN, ADMIN_API_KEY, JWT_SECRET_KEY) y migración a un gestor de secretos (Railway/Easypanel env vars ya cumplen esta función para producción — el problema es solo el archivo local).

---

## 📋 Prioridades

| Hallazgo | Severidad | Esfuerzo | Impacto |
|---|---|---|---|
| `deploy.yaml` invoca `make docker-build-env` inexistente — pipeline de build/push roto | Crítico | Bajo (renombrar target o fix Makefile) | Alto — desbloquea publicación de imágenes |
| CI no ejecuta `pytest` — sin gate de tests funcionales | Crítico | Medio (configurar Postgres service en Actions + `APP_ENV=test`) | Alto — atrapa regresiones antes de merge |
| `railway.toml` healthcheck apunta a `/api/v1/health` (stub siempre OK) en vez de `/health` (chequeo real) | Alto | Bajo (cambiar 1 línea) | Alto — Railway podrá detectar BD/cache caídos y reiniciar |
| Verificar si `.dockerignore` (`*.md`) excluye `app/core/prompts/*.md` rompiendo prompts en producción | Alto | Bajo (build de prueba + ajustar `.dockerignore`) | Alto — riesgo de romper el agente en prod |
| Sin security scanning (deps + imagen) en CI | Alto | Medio (agregar `pip-audit` + `trivy`) | Alto — reduce riesgo de CVEs en producción |
| `GF_SECURITY_ADMIN_PASSWORD=admin` + puerto 3000 expuesto | Alto (si está expuesto a internet) | Bajo (env var + revisar exposición de red) | Medio-Alto — evita acceso no autorizado a Grafana |
| Secretos reales en `.env.development` (no commiteados, pero presentes) — rotación pendiente de `todo/05` | Alto | Medio (rotar credenciales + verificar historial git) | Alto — previene compromiso de WhatsApp/CRM/Langfuse |
| Doble ejecución de `alembic upgrade head` en easypanel (compose `command` + entrypoint) | Medio | Bajo (quitar de uno de los dos) | Medio — reduce tiempo de arranque y ruido |
| Dockerfile no es multi-stage (build-essential en imagen final) + incluye `--group test` en prod | Medio | Medio (reestructurar Dockerfile en 2 etapas) | Medio — reduce tamaño/superficie de ataque |
| Imágenes `prom/prometheus:latest`, `grafana/grafana:latest`, `cadvisor:latest` sin pinnear | Medio | Bajo (fijar tags) | Medio — evita roturas por upgrades silenciosos |
| Producción (Easypanel) sin Prometheus/Grafana/alerting | Medio | Alto (desplegar stack de monitoreo en Easypanel o servicio externo) | Alto — visibilidad operativa en incidentes |
| Sin backup de `postgres-data` (memoria conversacional + datos de salud) | Medio | Medio (cron `pg_dump` + storage externo) | Alto — continuidad de negocio / cumplimiento |
| `version: '3.8'` obsoleto en ambos compose (todo/06) | Bajo | Trivial | Bajo — elimina warnings |
| `db`/`db-dev` depends_on inconsistente en `docker-compose.yml` (todo/07) | Alto | Bajo-Medio (corregir `depends_on` y/o consolidar servicios) | Medio — evita fallas intermitentes en `make stack-up` |
| Evals no programadas (todo/62) | Bajo | Medio (cron + reporting) | Medio — detecta regresiones de calidad del agente |
| `scripts/build-docker.sh` pasa secretos como `--build-arg` no usados | Bajo | Trivial (eliminar líneas) | Bajo-Medio — defensa en profundidad |

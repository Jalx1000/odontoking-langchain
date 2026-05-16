# Arquitectura de Componentes — Agent Production Platform

Vista estática de todos los servicios, cómo se agrupan y cómo se comunican entre sí. Para el flujo de actividad paso a paso ver [`whatsapp-event-flow.md`](./whatsapp-event-flow.md).

---

## Diagrama

```mermaid
graph TB
    classDef wa fill:#25d366,stroke:#075e54,color:#fff,font-weight:bold
    classDef front fill:#0b72f9,stroke:#0959c4,color:#fff,font-weight:bold
    classDef api fill:#0369a1,stroke:#075985,color:#fff
    classDef broker fill:#c2410c,stroke:#9a3412,color:#fff
    classDef agent fill:#6d28d9,stroke:#5b21b6,color:#fff
    classDef llm fill:#1d4ed8,stroke:#1e40af,color:#fff
    classDef db fill:#92400e,stroke:#78350f,color:#fff
    classDef obs fill:#b91c1c,stroke:#991b1c,color:#fff

    subgraph ACTORS["👤 Actores"]
        USER_WA["📱 Cliente WhatsApp\ntenant end-user"]:::wa
        ADMIN["🖥️ Operador Admin\nportal web"]:::front
    end

    subgraph N8N_LAYER["⚙️ n8n Workflows  (Railway container)"]
        WA_HOOK["📥 WhatsApp\nWebhook Trigger\nrecibe evento Meta"]:::api
        WA_OUT["📤 WhatsApp\nMessage Sender\nenvía respuesta"]:::api
    end

    subgraph FRONT_LAYER["🖥️ Admin Portal  04.agent-production-front  (Vite + React + shadcn/ui)"]
        LOGIN_P["🔐 Login Page\nJWT auth\nPOST /auth/login"]:::front
        DASH_P["📊 Dashboard\nstats globales\nper tenant"]:::front
        TENANT_P["🏢 Tenants\nCRUD + DLQ\ninline panel"]:::front
        BILL_P["💳 Billing\nreportes mensuales\nCSV export"]:::front
        API_CLI["📡 api.ts\nX-Admin-Key header\nTanStack Query"]:::front
    end

    subgraph BACKEND["🐍 Backend API  03.agent-production  (FastAPI + LangGraph · Railway)"]

        subgraph API_LAYER["🌐 API Layer  app/api/v1/"]
            WA_ROUTE["📨 /whatsapp/{slug}/webhook\nGET verify · POST receive\nrate limit 100/min"]:::api
            CHAT_ROUTE["💬 /chat\nPOST + SSE stream\nJWT protected"]:::api
            AUTH_ROUTE["🔑 /auth/login\nform-data\nJWT issue"]:::api
            ADMIN_ROUTE["🛡️ /admin/*\ntenants · stats\nDLQ · billing"]:::api
        end

        subgraph CORE_LAYER["⚙️ Core Services"]
            BUFFER["⏱️ MessageBufferService\n3s debounce window\nper wa_id"]:::broker
            BROKER["📦 MessageBroker\nRabbitMQ → Redis\n→ InMemory fallback"]:::broker
            LIMITER["🚦 Rate Limiter\nslowapi\nValkey backend"]:::api
            MID["🔗 Middleware\nMetrics · Logging\nCorrelation ID"]:::api
        end

        subgraph AGENT_LAYER["🤖 LangGraph Agent  app/core/langgraph/"]
            GRAPH["🔄 StateGraph\nchat node\ntool_call node"]:::agent
            TOOLS["🛠️ Tools\nduckduckgo · ask_human\nodontoking tools x7"]:::agent
            PROMPT["📄 System Prompt\napp/core/prompts/\nsystem.md"]:::agent
        end

        subgraph SVC_LAYER["🔧 Services  app/services/"]
            LLM_SVC["🧠 LLM Service\ntenacity retry 3x\nfallback circular"]:::llm
            MEM_SVC["🧬 Memory Service\nmem0 AsyncMemory\ncache-first lookup"]:::llm
            DB_SVC["🗄️ Database Service\nSQLModel sync\nusers · sessions"]:::db
            NOTIF["🚨 Notification\nSMTP SSL\nalert_processor"]:::obs
        end

    end

    subgraph INFRA["☁️ Infraestructura  Railway"]
        PG[("🐘 PostgreSQL\n+ pgvector\ncheckpoints · users\ntenants · usage_logs")]:::db
        RABBIT["🐰 RabbitMQ\nexchange wa.{tenant}\n+ DLX + DLQ"]:::broker
        VALKEY["⚡ Valkey/Redis\ncache · rate limits\nmessage buffer"]:::db
    end

    subgraph EXTERNAL["🌐 Servicios Externos"]
        CLAUDE["☁️ Anthropic Claude\nLLM primario\nAPI key"]:::llm
        OPENAI["☁️ OpenAI\nLLM fallback\nWhisper · embeddings"]:::llm
        META_API["📱 Meta Graph API\ngraph.facebook.com\nv25.0 · Cloud API"]:::wa
        LANGFUSE["🔭 Langfuse\nLLM tracing\ntokens · costo · eval"]:::obs
        PROM["📈 Prometheus\n+ Grafana\nmétricas API + LLM"]:::obs
    end

    %% WhatsApp inbound
    USER_WA -->|"mensaje"| META_API
    META_API -->|"POST webhook"| WA_HOOK
    WA_HOOK -->|"POST /whatsapp/{slug}/webhook"| WA_ROUTE
    WA_ROUTE --> BUFFER
    BUFFER --> BROKER
    BROKER <-->|"AMQP"| RABBIT
    BROKER --> GRAPH

    %% WhatsApp outbound
    GRAPH -->|"respuesta"| WA_OUT
    WA_OUT -->|"POST /messages"| META_API
    META_API -->|"entrega"| USER_WA

    %% Admin portal
    ADMIN --> LOGIN_P
    LOGIN_P -->|"form-data"| AUTH_ROUTE
    DASH_P & TENANT_P & BILL_P --> API_CLI
    API_CLI -->|"HTTPS + X-Admin-Key"| ADMIN_ROUTE
    ADMIN_ROUTE <-->|"tenants · usage_logs · DLQ"| PG

    %% Chat API
    CHAT_ROUTE --> GRAPH

    %% Agent internals
    GRAPH --> LLM_SVC
    GRAPH --> MEM_SVC
    GRAPH --> TOOLS
    GRAPH <-->|"prompts"| PROMPT

    %% LLM
    LLM_SVC -->|"primary"| CLAUDE
    LLM_SVC -->|"fallback"| OPENAI

    %% Memory
    MEM_SVC <-->|"cache"| VALKEY
    MEM_SVC <-->|"pgvector search + embeddings"| PG
    OPENAI -->|"text-embedding-3-small"| MEM_SVC

    %% Checkpointing
    GRAPH <-->|"AsyncPostgresSaver checkpoints"| PG

    %% Auth
    AUTH_ROUTE <-->|"users · sessions"| PG
    DB_SVC <--> PG

    %% Rate limiting
    LIMITER <-->|"counters"| VALKEY
    WA_ROUTE & CHAT_ROUTE & AUTH_ROUTE & ADMIN_ROUTE --> LIMITER
    WA_ROUTE & CHAT_ROUTE & AUTH_ROUTE & ADMIN_ROUTE --> MID

    %% Observability
    LLM_SVC -.->|"LLM traces"| LANGFUSE
    TOOLS -.->|"tool traces"| LANGFUSE
    MID -.->|"http metrics"| PROM
    LLM_SVC -.->|"llm_duration"| PROM
    BROKER -.->|"DLQ alert"| NOTIF
    WA_ROUTE -.->|"transcribe audio"| OPENAI
```

---

## Leyenda de Colores

| Color | Capa |
|-------|------|
| 🟢 Verde | WhatsApp / Meta |
| 🔵 Azul oscuro | Admin Portal (frontend) |
| 🔵 Azul | API FastAPI / middleware |
| 🟠 Naranja | Message Broker / Buffer |
| 🟣 Violeta | LangGraph Agent |
| 🔵 Azul brillante | LLM / Memory Services |
| 🟤 Marrón | Base de datos / Persistencia |
| 🔴 Rojo | Observabilidad / Alertas |

---

## Repositorios

| Repo | Path | Tecnología |
|------|------|-----------|
| Backend | `~/09.platzi/03.agent-production` | FastAPI · LangGraph · PostgreSQL |
| Frontend | `~/09.platzi/04.agent-production-front` | Vite · React · shadcn/ui |

## Infraestructura Railway

| Servicio | Container | Notas |
|---------|-----------|-------|
| API Backend | `03.agent-production` (Dockerfile) | Puerto 8000 |
| PostgreSQL | Railway Postgres plugin | pgvector extension |
| RabbitMQ | `rabbitmq` image | Exchange por tenant |
| Valkey | Railway Redis plugin | Cache + rate limiting |
| n8n | `n8nio/n8n` image | Workflows WhatsApp |

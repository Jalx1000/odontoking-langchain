# Flujo de Evento WhatsApp → Respuesta

Diagrama completo del ciclo de vida de un mensaje: desde que Meta envía el webhook hasta que el usuario recibe la respuesta, incluyendo broker, agente LangGraph, LLM, memoria y observabilidad.

---

## Diagrama de Actividades — Flujo Completo

```mermaid
flowchart TD
    classDef wa fill:#25d366,stroke:#075e54,color:#fff,font-weight:bold
    classDef api fill:#0369a1,stroke:#075985,color:#fff
    classDef broker fill:#c2410c,stroke:#9a3412,color:#fff
    classDef agent fill:#6d28d9,stroke:#5b21b6,color:#fff
    classDef llm fill:#1d4ed8,stroke:#1e40af,color:#fff
    classDef db fill:#92400e,stroke:#78350f,color:#fff
    classDef obs fill:#b91c1c,stroke:#991b1c,color:#fff
    classDef gate fill:#1f2937,stroke:#111827,color:#e5e7eb
    classDef err fill:#7f1d1d,stroke:#991b1b,color:#fca5a5

    START(["📱 Meta envía evento\nPOST /{tenant_slug}/webhook\nJSON payload"]):::wa

    subgraph INGRESS["🌐 FastAPI — Capa de Ingreso  app/api/v1/whatsapp.py"]
        RL["🚦 Rate Limiter IP\n100 req/min · slowapi\n+ Valkey backend"]:::api
        TC{"Tenant\nválido?"}:::gate
        PP["📦 Parse Payload\nWhatsAppWebhookPayload\nPydantic v2 validation"]:::api
        MT{"Tipo de\nmensaje?"}:::gate
    end

    subgraph MSGPROC["📨 Procesamiento de Mensaje  app/services/whatsapp_client.py"]
        TXT["📝 Texto\ndirecto"]:::api
        AUD["⬇️ Descargar audio\ngraph.facebook.com\n/media/{id}"]:::api
        WHI["🎤 Transcribir\nOpenAI Whisper API\nmodelo whisper-1 · es"]:::api
        INT["🔘 Interactive\nbutton_reply / list_reply\nextraer title"]:::api
        UNS["🚫 Tipo no soportado\nimage / video / doc\nenviar aviso al user"]:::err
    end

    subgraph BUFBROKER["⏱️ Buffer & Message Broker  app/services/message_buffer.py · app/core/broker.py"]
        URl{"User rate\nlimit?\n20 msg/60s"}:::gate
        BKG["✅ Mark as read\n+ Typing indicator\n(background tasks)"]:::api
        BUF["📥 MessageBufferService\nenqueue wa_id + text\n3s debounce window"]:::broker
        PUB["📤 Broker publish\ntenant slug + payload\nRabbitMQ → Redis → InMemory"]:::broker
        RTY{"Fallo al\nprocesar?\nretries < 3"}:::gate
        DLQ["☠️ Dead Letter Queue\n+ Email Alert SMTP\nnotify via alert_processor"]:::err
    end

    subgraph AGENTLAYER["🤖 LangGraph Agent  app/core/langgraph/"]
        GAT["⚡ asyncio.gather\nget_state + memory.search\nconcurrente sin bloqueo"]:::agent
        INT2{"¿Interrupted?\nstate.next set?"}:::gate
        INV["🔄 graph.ainvoke\nthread_id = wa_id\no Command resume"]:::agent
        CHAT["💬 chat node\nBuild system prompt\n+ LLM call"]:::agent
        TCK{"¿Tool\ncalls\nen resp?"}:::gate
        TOOL["⚙️ tool_call node\nasyncio.gather tools\nmax_retries = 3"]:::agent
        EXT["📤 Extraer respuesta\nJSON parse → campo mensaje\nfallback: raw text"]:::agent
    end

    subgraph LLMLAYER["🧠 LLM Service  app/services/llm/service.py"]
        LLM["🤖 llm_service.call\ntenacity @retry\n3 intentos · backoff exp"]:::llm
        LF{"¿Error?\n¿Fallback?"}:::gate
        FB["🔄 Cambiar modelo\nLLMRegistry circular\nfallback chain"]:::llm
        CL["☁️ Anthropic Claude\nModelo primario\n60s total timeout"]:::llm
        OA["☁️ OpenAI\nModelo fallback\ncircular index"]:::llm
    end

    subgraph MEMLAYER["🧬 Memory Service  app/services/memory.py"]
        CHK{"Cache hit?\nValkey / Redis\nTTL lookup"}:::gate
        VEC["🔍 pgvector search\nmem0 AsyncMemory\nOpenAI text-embedding-3-small"]:::db
        MR["📋 long_term_memory\nresultado formateado\n→ contexto del sistema"]:::db
    end

    subgraph PERSLAYER["🗄️ Persistencia  PostgreSQL + pgvector"]
        CPT["📌 AsyncPostgresSaver\ncheckpoints + blobs\nper thread_id"]:::db
        PST["💾 Persist messages\nhistorial completo\n(background task)"]:::db
        MAD["✨ mem0.add\nactualizar embeddings\n(background · fire & forget)"]:::db
    end

    subgraph RESPLAYER["📤 Respuesta a WhatsApp  app/services/whatsapp_client.py"]
        OPT{"¿Opciones\nnumeradas\nen texto?"}:::gate
        INT3["🔘 send_interactive\nButtons ≤3 / List 4-10\nPOST /messages Cloud API"]:::wa
        TXO["📝 send_text_message\nPOST /messages\ngraph.facebook.com v25.0"]:::wa
    end

    subgraph OBS["📊 Observabilidad — en cada paso"]
        PRO["📈 Prometheus\nhttp_requests_total\nllm_inference_duration_seconds"]:::obs
        LFU["🔭 Langfuse\ntraza LLM + tools\ntokens · latencia · costo"]:::obs
        LOG["📝 structlog JSON\nrequest_id · session_id\ntodos los eventos"]:::obs
        ALT["🚨 Email Alert\nSMTP SSL port 465\non DLQ / errors"]:::obs
    end

    END_(["📱 Usuario WhatsApp\nrecibe respuesta"]):::wa

    START --> RL --> TC
    TC -->|"❌ token inválido"| DENY["HTTP 200\nNo reintentar"]:::err
    TC -->|"✅ OK"| PP --> MT

    MT -->|text| TXT
    MT -->|audio| AUD --> WHI
    MT -->|interactive| INT
    MT -->|"image/video/doc"| UNS --> TXO

    TXT & WHI & INT --> URl
    URl -->|"❌ limitado"| DROP["⏭ Descartar\nlog: wa_rate_limited"]:::err
    URl -->|"✅ OK"| BKG --> BUF --> PUB

    PUB -->|"✅ procesado"| GAT
    PUB -->|"❌ fallo"| RTY
    RTY -->|"sí"| PUB
    RTY -->|"no · >= 3"| DLQ
    DLQ -.->|"alerta"| ALT

    GAT --> CHK
    CHK -->|miss| VEC --> MR
    CHK -->|hit| MR
    GAT --> INT2
    INT2 -->|"resume"| INV
    INT2 -->|"fresh"| INV
    MR --> CHAT
    INV --> CHAT

    CHAT --> LLM --> CL
    LLM -->|"error"| LF
    LF -->|"sí"| FB --> OA
    CL & OA --> TCK

    TCK -->|"sí"| TOOL --> CHAT
    TCK -->|"no · END"| EXT

    EXT --> CPT --> PST --> MAD
    MAD -.->|"embeddings"| VEC

    EXT --> OPT
    OPT -->|"sí"| INT3 --> END_
    OPT -->|"no"| TXO --> END_

    RL -.->|"http_requests_total"| PRO
    CHAT -.->|"llm_inference_duration"| PRO
    LLM -.->|"LLM trace"| LFU
    TOOL -.->|"tool trace"| LFU
    PUB -.->|"broker_published"| LOG
    EXT -.->|"agent_response"| LOG
```

---

## Descripción de Capas

### 1. Ingreso (`app/api/v1/whatsapp.py`)
- **Rate limit global**: 100 req/min por IP via `slowapi` con backend Valkey
- **Verificación de tenant**: lookup en registro + comparación de `verify_token`
- **Parse Pydantic**: `WhatsAppWebhookPayload` valida estructura del webhook de Meta

### 2. Procesamiento de Mensaje (`app/services/whatsapp_client.py`)
| Tipo | Acción |
|------|--------|
| `text` | Texto directo |
| `audio` | Descarga binario → Whisper API (es) |
| `interactive` | Extrae `button_reply.title` o `list_reply.title` |
| `image/video/doc/sticker` | Envía mensaje "no soportado" |

### 3. Buffer & Broker (`app/services/message_buffer.py`, `app/core/broker.py`)
- **Debounce 3s**: acumula mensajes del mismo `wa_id` antes de procesar
- **Prioridad de broker**: `RabbitMQ (aio-pika)` → `Redis Streams` → `InMemory`
- **Retry con DLQ**: 3 intentos → Dead Letter Queue → Email alert vía SMTP

### 4. LangGraph Agent (`app/core/langgraph/`)
- **Concurrencia**: `asyncio.gather(get_state, memory.search)` evita latencia secuencial
- **Nodo `chat`**: construye prompt con memoria + llama LLM → decide si usar tools
- **Nodo `tool_call`**: ejecuta tools en paralelo → regresa a `chat`
- **Interrupts**: si `state.next` está set → `Command(resume=texto)` en vez de fresh invoke

### 5. LLM Service (`app/services/llm/service.py`)
- **Tenacity**: 3 reintentos con backoff exponencial por modelo
- **Fallback circular**: `LLMRegistry` → si modelo A falla, pasa a B, luego C, luego A
- **Timeout global**: `LLM_TOTAL_TIMEOUT = 60s` cubre todo el loop de retry+fallback

### 6. Memory Service (`app/services/memory.py`)
- **Cache-first**: busca en Valkey antes de ir a pgvector
- **mem0 AsyncMemory**: embeddings con `text-embedding-3-small`, almacenados en pgvector
- **Actualización async**: `mem0.add()` corre como background task tras cada respuesta

### 7. Persistencia (PostgreSQL)
- **`AsyncPostgresSaver`**: checkpoints del grafo por `thread_id` (= `wa_id`)
- **Historial de chat**: guardado en background, no bloquea la respuesta
- **pgvector**: almacena embeddings de memoria de largo plazo por `user_id`

### 8. Respuesta WhatsApp (`app/services/whatsapp_client.py`)
- **Detección de opciones**: regex `\d+\)\s*([^\n]+)` busca listas numeradas
- **Interactive ≤3 opciones**: botones (`button_reply`)
- **Interactive 4–10 opciones**: lista desplegable (`list_reply`)
- **Plain text**: fallback para respuestas sin opciones

### 9. Observabilidad (transversal)
| Herramienta | Qué captura |
|------------|-------------|
| **Prometheus** | `http_requests_total`, `llm_inference_duration_seconds` (histogram por modelo) |
| **Langfuse** | Trazas LLM completas: tokens input/output, latencia, costo, tool calls |
| **structlog** | Todos los eventos con `request_id`, `session_id`, `wa_id` como contexto |
| **Email alerts** | `alert_processor` en cada `logger.error()` con debounce de 300s por evento |

---

## Rutas de Error

| Error | Comportamiento |
|-------|---------------|
| Tenant inválido | HTTP 200 (evita reintentos de Meta) |
| Payload malformado | HTTP 200 + log exception |
| Audio no transcribible | Mensaje "no pude entender el audio" al usuario |
| User rate limited | Descarte silencioso + log warning |
| LLM timeout (>60s) | Mensaje "consulta tardando más de lo esperado" |
| Todos los modelos fallan | Mensaje "ocurrió un error, intente de nuevo" |
| Mensaje a DLQ | Email alert + log error + in-memory DLQ para admin panel |

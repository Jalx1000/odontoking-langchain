# Para el equipo del CRM — subir el throttle del token del agente (429)

> De: equipo del agente IA (Sofía / Kohlberg). CRM: Krayin/Laravel, `kohlberg.sofopolis.com`.
> **Urgente-ish:** el agente va a pasar a escribir el lead **en vivo** (cada dato del
> cliente = un PUT), así que las llamadas por conversación suben de ~1 a **~5-6**. Con
> el throttle actual eso va a disparar `429 Too Many Attempts` seguido.

## El síntoma que ya vemos

Con el token Sanctum del agente, ráfagas de llamadas devuelven:

```
HTTP 429  { "message": "Too Many Attempts." }
```

En nuestros logs sale como `crm_reply_error status=429`. Cuando pega, la respuesta
al cliente se degrada o se pierde. **No reintentamos el 429** (reintentar hunde más
el bucket), así que la única solución real es del lado del CRM: subir/segmentar el
límite para el token del agente.

## Por qué empeora ahora

Hoy el agente escribe el pedido **una vez**, al confirmar. Vamos a cambiarlo a
**registro incremental**: apenas el cliente dice ciudad → PUT; nombre → PUT; edad →
PUT; cada vino → PUT; confirmar → PUT. Es a pedido del negocio (que el lead quede
registrado aunque el cliente abandone). Resultado: **~5-6 requests por conversación**
en vez de 1, a veces en pocos segundos.

## Lo que pedimos (una de estas, en orden de preferencia)

### Opción A — límite dedicado y alto para el token del agente (recomendada)

En Laravel el throttle vive en `RouteServiceProvider::configureRateLimiting()` (o
`app/Providers/AppServiceProvider`). El default de `throttle:api` es **60/min por
usuario**. Denle al usuario/token del agente su propio limiter, mucho más alto:

```php
use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Support\Facades\RateLimiter;

RateLimiter::for('api', function (Request $request) {
    $user = $request->user();

    // El usuario del agente IA: límite amplio (o sin límite).
    if ($user && $user->id === (int) config('whatsapp.agent_user_id')) {
        return Limit::perMinute(600)->by('agent:'.$user->id);
        // o directamente: return Limit::none();  // si confían en el agente
    }

    // Resto de usuarios: como está hoy.
    return Limit::perMinute(60)->by(optional($user)->id ?: $request->ip());
});
```

Clave: **keyed por el token/usuario del agente**, no por IP compartida, y con un
techo que aguante ráfagas de 6-10 requests en pocos segundos (600/min da holgura).

### Opción B — subir el límite global de `throttle:api`

Menos fino pero simple: subir el `perMinute` general (p. ej. de 60 a 300). Afecta a
todos los usuarios, por eso preferimos A.

### Opción C — exceptuar del throttle las rutas que usa el agente

Sacar `throttle:api` (o ponerle un límite alto propio) al grupo de rutas que toca el
agente: `POST/PUT /api/v1/leads`, `PUT /api/v1/contacts/persons/{id}`,
`GET /api/v1/products`, `GET /api/pedidos/por-telefono`, `POST /api/v1/whatsapp/...`.

## Lo que ya hacemos nosotros para no abusar

- **No reintentamos 429** (fail-fast, no ahondamos el bucket).
- **Cacheamos el catálogo** (`GET /api/v1/products`) en memoria con TTL corto, así la
  llamada más pesada corre una vez por ventana entre todas las conversaciones.
- Solo hacemos PUT **cuando llega un dato nuevo**, no en cada turno.
- Aun así, el incremental nos deja en ~5-6 escrituras/charla — de ahí el pedido.

## Un detalle que ayuda: `Retry-After`

Si el 429 igual ocurre, devuelvan el header **`Retry-After`** (segundos). Hoy no lo
reintentamos, pero con ese header podemos esperar y reintentar UNA vez en vez de
perder el mensaje. Sin el header, no hay forma de saber cuánto esperar.

## Resumen de una línea

> El agente va a escribir el lead en vivo (~5-6 requests/conversación). Denle al
> **token del agente un limiter propio y alto** (`RateLimiter::for('api')` keyed por
> ese usuario, p. ej. 600/min o `Limit::none()`), y devuelvan `Retry-After` en el 429.

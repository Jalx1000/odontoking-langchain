"""Unit tests for IMPRIMIR city routing, lead guards, and the cotizacion tool.

Pure logic + the cotizacion tool with a stubbed httpx client — no live CRM calls.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.langgraph.tools import crm
from app.core.langgraph.tools.crm import (
    _ctx_ids,
    _initial_stage_id,
    _is_unattended,
    _lead_stage_name,
    _marca_de_sku,
    _resolve_pipeline_id,
    _route_lead_pipeline,
    crear_cotizacion,
    derivar_a_asesor,
    enviar_material,
    mostrar_opciones,
    quien_atiende,
)


class TestCityRouting:
    """_resolve_pipeline_id maps free-text cities to the (non-correlative) pipeline ids."""

    @pytest.mark.parametrize(
        "ciudad,expected",
        [
            ("Santa Cruz", (1, True)),
            ("scz", (1, True)),
            ("Cochabamba", (8, True)),
            ("cbba", (8, True)),
            ("La Paz", (7, True)),
            ("Potosí", (4, True)),
            ("potosi", (4, True)),
            ("Oruro", (6, True)),
            ("Sucre", (9, True)),
        ],
    )
    def test_known_cities(self, ciudad, expected):
        """Each listed city (and its aliases) resolves to its exact pipeline id."""
        assert _resolve_pipeline_id(ciudad) == expected

    @pytest.mark.parametrize(
        "ciudad", ["Otra ciudad", "otra ciudad", "Otra", "Tarija", "cualquier cosa", "", None]
    )
    def test_unknown_city_falls_back_to_sin_ciudad(self, ciudad):
        """Unknown/empty city — and the "Otra ciudad" menu pick — → pipeline 10, NEVER Santa Cruz."""
        pipeline_id, recognised = _resolve_pipeline_id(ciudad)
        assert pipeline_id == 10
        assert recognised is False


class TestUnattendedGuard:
    """Only the untouched auto-created lead ('No atendido') may be moved/enriched."""

    @pytest.mark.parametrize("name", ["No atendido", "no atendido", "  NO ATENDIDO  "])
    def test_unattended_stage_is_movable(self, name):
        """The initial stage (any case/spacing) is movable."""
        assert _is_unattended({"lead_pipeline_stage": {"name": name}}) is True

    @pytest.mark.parametrize("name", ["En proceso", "Ganado", "Perdido"])
    def test_advanced_stage_is_not_touched(self, name):
        """A lead an advisor already advanced is left alone."""
        assert _is_unattended({"lead_pipeline_stage": {"name": name}}) is False

    def test_unknown_stage_defaults_movable(self):
        """When the stage can't be read, treat as movable (fresh lead is the common case)."""
        assert _is_unattended({}) is True
        assert _lead_stage_name({}) == ""


def _patch_httpx(monkeypatch, *, status=201, payload=None):
    """Patch crm.httpx.AsyncClient with a fake that records the POST; returns the capture dict."""
    box = {"payload": payload if payload is not None else {"quote_id": 1}, "url": None, "json": None}

    class _Resp:
        status_code = status
        headers = {"content-type": "application/json"}

        def json(self):
            return box["payload"]

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            box["url"] = url
            box["json"] = json
            return _Resp()

        async def get(self, url, params=None, headers=None):
            box["url"] = url
            box["params"] = params
            return _Resp()

    monkeypatch.setattr(crm.httpx, "AsyncClient", _Client)
    return box


class TestCrearCotizacion:
    """crear_cotizacion POSTs to the productos cotizacion endpoint; conversation_id comes from config."""

    @pytest.mark.asyncio
    async def test_no_conversation_id_returns_text_error(self):
        """No conversation_id → graceful text error, never a crash (the LLM never passes ids)."""
        out = await crear_cotizacion.ainvoke({"sku": "CM_00002", "cantidad": 25}, {"metadata": {}})
        assert "No hay una conversación activa" in out

    @pytest.mark.asyncio
    async def test_posts_expected_body_and_returns_payload(self, monkeypatch):
        """POSTs sku/cantidad/criterios/nit to /conversations/{id}/cotizacion and returns the JSON."""
        box = _patch_httpx(monkeypatch, status=201, payload={"quote_id": 114, "total": 325})
        out = await crear_cotizacion.ainvoke(
            {
                "sku": "CM_00002", "cantidad": 25,
                "criterios": {"Tamaño": "50 L", "Canal": "HORECA", "Zona": "Santa Cruz"},
                "nit": "1234567019", "empresa": "El Fogón",
            },
            {"metadata": {"conversation_id": 123}},
        )
        assert box["url"].endswith("/api/v1/productos/conversations/123/cotizacion")
        assert box["json"] == {
            "sku": "CM_00002", "cantidad": 25, "marca": "Magia Verde",
            "criterios": {"Tamaño": "50 L", "Canal": "HORECA", "Zona": "Santa Cruz"},
            "nit": "1234567019", "empresa": "El Fogón",
        }
        assert '"quote_id": 114' in out

    @pytest.mark.asyncio
    async def test_non_bag_sku_gets_imprimir_marca(self, monkeypatch):
        """A non-bag product (e.g. Tapas) carries marca 'Imprimir' in the quote body."""
        box = _patch_httpx(monkeypatch)
        await crear_cotizacion.ainvoke(
            {"sku": "CM_00004", "cantidad": 30}, {"metadata": {"conversation_id": 9}}
        )
        assert box["json"]["marca"] == "Imprimir"

    @pytest.mark.asyncio
    async def test_ciudad_travels_in_body_for_advisor_routing(self, monkeypatch):
        """`ciudad` (real city, not Zona) is sent so the CRM can pick the round-robin advisor."""
        box = _patch_httpx(monkeypatch)
        await crear_cotizacion.ainvoke(
            {"sku": "CM_00003", "cantidad": 5, "criterios": {"Color": "Negro"}, "ciudad": "La Paz"},
            {"metadata": {"conversation_id": 9}},
        )
        assert box["json"]["ciudad"] == "La Paz"


class TestQuienAtiende:
    """quien_atiende GETs /productos/responsables read-only and passes the JSON through."""

    @pytest.mark.asyncio
    async def test_gets_responsables_with_sku_and_city(self, monkeypatch):
        """Sends sku+ciudad as query params and returns the CRM payload verbatim."""
        box = _patch_httpx(
            monkeypatch, status=200,
            payload={"cobertura": "local", "siguiente": {"nombre": "Gabriela Marconi"}},
        )
        out = await quien_atiende.ainvoke({"sku": "CM_00003", "ciudad": "La Paz"})
        assert box["url"].endswith("/api/v1/productos/responsables")
        assert box["params"] == {"sku": "CM_00003", "ciudad": "La Paz"}
        assert "Gabriela Marconi" in out

    @pytest.mark.asyncio
    async def test_unknown_sku_returns_404_message(self, monkeypatch):
        """A 404 (unknown SKU) surfaces the CRM message, never crashes."""
        _patch_httpx(monkeypatch, status=404, payload={"message": "No existe un producto con el SKU X."})
        out = await quien_atiende.ainvoke({"sku": "NADA"})
        assert "No existe un producto" in out


class TestEnviarMaterial:
    """enviar_material POSTs to /media; criterios select a single variant's ficha."""

    @pytest.mark.asyncio
    async def test_criterios_travel_in_body(self, monkeypatch):
        """`criterios` are forwarded so the CRM sends only that variant's attachment."""
        box = _patch_httpx(
            monkeypatch, status=200,
            payload={"enviados": 1, "sku": "CM_00002", "variante": "50 L", "alcance": "variante"},
        )
        out = await enviar_material.ainvoke(
            {"sku": "CM_00002", "tipo": "documento", "criterios": {"Tamaño": "50 L"}},
            {"metadata": {"conversation_id": 123}},
        )
        assert box["url"].endswith("/api/v1/productos/conversations/123/media")
        assert box["json"]["criterios"] == {"Tamaño": "50 L"}
        assert "50 L" in out  # names the variant it actually sent

    @pytest.mark.asyncio
    async def test_no_criterios_omits_the_key(self, monkeypatch):
        """Without criterios the body carries no `criterios` key (back-compat: send everything)."""
        box = _patch_httpx(monkeypatch, status=200, payload={"enviados": 1, "sku": "CM_00002"})
        await enviar_material.ainvoke(
            {"sku": "CM_00002", "tipo": "imagen", "cantidad": 1}, {"metadata": {"conversation_id": 9}}
        )
        assert "criterios" not in box["json"]

    @pytest.mark.asyncio
    async def test_alcance_producto_warns_not_the_specific_ficha(self, monkeypatch):
        """`alcance: producto` (variant had no own ficha) tells the agent not to claim the specific one."""
        _patch_httpx(
            monkeypatch, status=200,
            payload={"enviados": 1, "sku": "CM_00003", "variante": None, "alcance": "producto"},
        )
        out = await enviar_material.ainvoke(
            {"sku": "CM_00003", "tipo": "documento", "criterios": {"Color": "Negro"}},
            {"metadata": {"conversation_id": 9}},
        )
        assert "general" in out.lower()


class TestDerivarSignal:
    """derivar_a_asesor is a pure signal that now carries sku+ciudad for advisor routing."""

    @pytest.mark.asyncio
    async def test_signal_includes_sku_and_ciudad(self):
        """The returned signal echoes reason/sku/ciudad so the router forwards them to the handoff."""
        out = await derivar_a_asesor.ainvoke(
            {"conversation_id": 2, "reason": "quiere hablar", "sku": "CM_00003", "ciudad": "Santa Cruz"}
        )
        d = json.loads(out)
        assert d["status"] == "handoff_signaled"
        assert d["sku"] == "CM_00003" and d["ciudad"] == "Santa Cruz"

    @pytest.mark.asyncio
    async def test_price_and_total_are_never_sent(self, monkeypatch):
        """The agent must never send a price/total — the CRM resolves them (a model error stays cheap)."""
        box = _patch_httpx(monkeypatch)
        await crear_cotizacion.ainvoke(
            {"sku": "CM_00002", "cantidad": 25}, {"metadata": {"conversation_id": 9}}
        )
        assert "precio" not in box["json"] and "total" not in box["json"]

    @pytest.mark.asyncio
    async def test_422_surfaces_crm_message(self, monkeypatch):
        """A 422 (missing axis / unassociated contact) surfaces the CRM message verbatim to the model."""
        _patch_httpx(monkeypatch, status=422, payload={"message": "Faltan datos para cotizar: Canal."})
        out = await crear_cotizacion.ainvoke(
            {"sku": "CM_00002", "cantidad": 25}, {"metadata": {"conversation_id": 123}}
        )
        assert "Faltan datos para cotizar: Canal." in out


class TestMarcaDeSku:
    """_marca_de_sku: bags are 'Magia Verde', the rest of the catalog is 'Imprimir'."""

    def test_bags_are_magia_verde(self):
        """CM_00002 (Bolsas Magia Verde) → 'Magia Verde' (case/space-insensitive)."""
        assert _marca_de_sku("CM_00002") == "Magia Verde"
        assert _marca_de_sku("  cm_00002 ") == "Magia Verde"

    def test_everything_else_is_imprimir(self):
        """Hules/Stretch/Tapas and any unknown SKU → 'Imprimir'."""
        assert _marca_de_sku("CM_00003") == "Imprimir"
        assert _marca_de_sku("CM_00001") == "Imprimir"
        assert _marca_de_sku("CM_00004") == "Imprimir"
        assert _marca_de_sku("") == "Imprimir"


class TestCtxIds:
    """_ctx_ids reads injected ids from config.metadata (the LLM never sees them)."""

    def test_reads_metadata(self):
        """lead_id/person_id come from config.metadata."""
        cfg = {"metadata": {"lead_id": 86, "person_id": 239}}
        assert _ctx_ids(cfg) == (86, 239)

    def test_missing_config_is_none_none(self):
        """No config / no metadata → (None, None), never a crash."""
        assert _ctx_ids(None) == (None, None)
        assert _ctx_ids({}) == (None, None)


class TestInitialStage:
    """_initial_stage_id resolves the lowest-sort_order stage (never hardcode stage ids)."""

    @pytest.mark.asyncio
    async def test_picks_lowest_sort_order_list(self, monkeypatch):
        """Given a list of stages, the one with the smallest sort_order wins."""
        resp = MagicMock()
        resp.json.return_value = {"data": {"stages": [
            {"id": 41, "sort_order": 1}, {"id": 42, "sort_order": 2}, {"id": 40, "sort_order": 0},
        ]}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        assert await _initial_stage_id(MagicMock(), 8) == 40

    @pytest.mark.asyncio
    async def test_picks_lowest_sort_order_dict(self, monkeypatch):
        """Krayin sometimes returns stages as a dict keyed by id — still resolved correctly."""
        resp = MagicMock()
        resp.json.return_value = {"data": {"stages": {
            "a": {"id": 7, "sort_order": 5}, "b": {"id": 3, "sort_order": 1},
        }}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        assert await _initial_stage_id(MagicMock(), 9) == 3

    @pytest.mark.asyncio
    async def test_no_stages_returns_none(self, monkeypatch):
        """No stages → None so the caller omits the stage and lets Krayin default it."""
        resp = MagicMock()
        resp.json.return_value = {"data": {"stages": []}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        assert await _initial_stage_id(MagicMock(), 10) is None


class TestResolveCiudadOptionId:
    """The lead `ciudad` is a SELECT: its value must be the option id (40-50), never the label."""

    @pytest.mark.parametrize(
        "ciudad,expected",
        [
            ("Santa Cruz", 40), ("scz", 40),
            ("La Paz", 41), ("lp", 41),
            ("Cochabamba", 42), ("cbba", 42),
            ("Sucre", 44), ("Oruro", 45),
            ("Potosí", 46), ("potosi", 46),
        ],
    )
    def test_known_cities_map_to_option_ids(self, ciudad, expected):
        """Each known city (and alias) resolves to its select option id."""
        assert crm._resolve_ciudad_option_id(ciudad) == expected

    @pytest.mark.parametrize("ciudad", ["Tarija", "Trinidad", "otra ciudad", "", None])
    def test_unknown_city_returns_none(self, ciudad):
        """A city without a mapped option id → None (we send no ciudad, never a guess)."""
        assert crm._resolve_ciudad_option_id(ciudad) is None


class TestRouteLeadPipeline:
    """_route_lead_pipeline moves the lead to its city's pipeline AND sets ciudad in one PUT."""

    @pytest.mark.asyncio
    async def test_puts_pipeline_stage_and_ciudad_for_the_city(self, monkeypatch):
        """La Paz → ONE PUT with pipeline 7, the initial stage, and ciudad option id 41."""
        req = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(crm, "_request", req)
        monkeypatch.setattr(crm, "_initial_stage_id", AsyncMock(return_value=55))
        pid = await _route_lead_pipeline(MagicMock(), 479, "La Paz")
        assert pid == 7
        _, method, path = req.call_args.args
        assert method == "PUT" and path == "/api/v1/leads/479"
        assert req.call_args.kwargs["json"] == {
            "lead_pipeline_id": 7, "lead_pipeline_stage_id": 55, "ciudad": 41,
        }

    @pytest.mark.asyncio
    async def test_stage_always_present_so_no_500(self, monkeypatch):
        """The PUT ALWAYS carries lead_pipeline_stage_id when resolvable (the 500's root cause)."""
        req = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(crm, "_request", req)
        monkeypatch.setattr(crm, "_initial_stage_id", AsyncMock(return_value=1))
        await _route_lead_pipeline(MagicMock(), 798, "Santa Cruz")
        body = req.call_args.kwargs["json"]
        assert "lead_pipeline_stage_id" in body and body["ciudad"] == 40

    @pytest.mark.asyncio
    async def test_unknown_city_routes_without_ciudad(self, monkeypatch):
        """Tarija → pipeline 10, no stage (unresolved) and NO ciudad key (no option id guess)."""
        req = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(crm, "_request", req)
        monkeypatch.setattr(crm, "_initial_stage_id", AsyncMock(return_value=None))
        pid = await _route_lead_pipeline(MagicMock(), 479, "Tarija")
        assert pid == 10
        assert req.call_args.kwargs["json"] == {"lead_pipeline_id": 10}

    @pytest.mark.asyncio
    async def test_blank_city_is_a_noop(self, monkeypatch):
        """No city → no move (the lead stays where the CRM created it)."""
        req = AsyncMock()
        monkeypatch.setattr(crm, "_request", req)
        assert await _route_lead_pipeline(MagicMock(), 479, "") is None
        req.assert_not_called()


def _patch_httpx_record_all(monkeypatch, *, status=200, payload=None, status_by_path=None):
    """Patch crm.httpx.AsyncClient recording EVERY POST in order; returns the list.

    `status_by_path` maps a url substring → status, so one call can make /media fail while
    /interactive succeeds.
    """
    posts: list[dict] = []

    def _code_for(url):
        for frag, code in (status_by_path or {}).items():
            if frag in url:
                return code
        return status

    class _Resp:
        def __init__(self, url, code):
            self.url = url
            self.status_code = code
            self.headers = {"content-type": "application/json"}

        def json(self):
            return payload if payload is not None else {"enviados": 1}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            posts.append({"url": url, "json": json})
            return _Resp(url, _code_for(url))

        async def get(self, url, params=None, headers=None):
            return _Resp(url, _code_for(url))

    monkeypatch.setattr(crm.httpx, "AsyncClient", _Client)
    return posts


_TAMANO_MENU = [
    {"id": t, "titulo": f"{t} (x)"} for t in ("35 L", "50 L", "75 L", "140 L", "200 L")
]
_CANAL_MENU = [
    {"id": "HOGAR", "titulo": "Para mi casa"},
    {"id": "TRADICIONAL", "titulo": "Tienda / mercado"},
    {"id": "HORECA", "titulo": "Restaurante / hotel"},
    {"id": "EMPRESARIAL", "titulo": "Empresa / oficina"},
    {"id": "DISTRIBUIDOR", "titulo": "Quiero revender"},
]


class TestImagenBolsasAuto:
    """Bolsas Magia Verde: the cover image is sent from code alongside the size menu."""

    def test_detecta_menu_tamano_por_id(self):
        """The 5-size menu is recognised by its axis ids ('35 L'…'200 L')."""
        assert crm._es_menu_tamano_bolsas(_TAMANO_MENU) is True

    def test_detecta_menu_tamano_por_titulo(self):
        """It is recognised even if the id is generic but the title carries the measure."""
        ops = [
            {"id": f"op{i}", "titulo": t}
            for i, t in enumerate(
                ("35 L (60x63)", "50 L (65x80)", "75 L (78x95)", "140 L (90x110)", "200 L XXL")
            )
        ]
        assert crm._es_menu_tamano_bolsas(ops) is True

    def test_no_detecta_menu_canal(self):
        """The channel (use) menu is NOT a size menu — no image must fire there."""
        assert crm._es_menu_tamano_bolsas(_CANAL_MENU) is False

    def test_no_detecta_menu_ciudad(self):
        """The city menu is not a size menu either."""
        ciudad = [
            {"id": "Santa Cruz", "titulo": "Santa Cruz"},
            {"id": "La Paz", "titulo": "La Paz"},
            {"id": "Cochabamba", "titulo": "Cochabamba"},
            {"id": "Otra ciudad", "titulo": "Otra ciudad"},
        ]
        assert crm._es_menu_tamano_bolsas(ciudad) is False

    @pytest.mark.asyncio
    async def test_menu_tamano_posts_image_before_menu(self, monkeypatch):
        """Showing the size menu first POSTs the CM_00002 image, then the interactive menu."""
        posts = _patch_httpx_record_all(monkeypatch, payload={"enviados": 1, "sku": "CM_00002"})
        await mostrar_opciones.ainvoke(
            {"cuerpo": "¿Qué tamaño o medida necesita?", "opciones": _TAMANO_MENU},
            {"metadata": {"conversation_id": 77}},
        )
        assert len(posts) == 2
        assert posts[0]["url"].endswith("/api/v1/productos/conversations/77/media")
        assert posts[0]["json"] == {"sku": "CM_00002", "tipo": "imagen", "cantidad": 1}
        assert posts[1]["url"].endswith("/api/v1/whatsapp/conversations/77/interactive")

    @pytest.mark.asyncio
    async def test_menu_canal_does_not_post_image(self, monkeypatch):
        """The channel menu must NOT trigger an image: only the interactive POST happens."""
        posts = _patch_httpx_record_all(monkeypatch, payload={})
        await mostrar_opciones.ainvoke(
            {"cuerpo": "¿Para qué uso la necesita?", "opciones": _CANAL_MENU},
            {"metadata": {"conversation_id": 77}},
        )
        assert len(posts) == 1
        assert posts[0]["url"].endswith("/interactive")

    @pytest.mark.asyncio
    async def test_image_failure_does_not_block_menu(self, monkeypatch):
        """If the image POST fails, the size menu is still shown (best-effort)."""
        posts = _patch_httpx_record_all(monkeypatch, status=200, status_by_path={"/media": 500})
        out = await mostrar_opciones.ainvoke(
            {"cuerpo": "¿Qué tamaño o medida necesita?", "opciones": _TAMANO_MENU},
            {"metadata": {"conversation_id": 77}},
        )
        # The image (/media) was attempted and failed; the menu (/interactive) still ran.
        assert any(p["url"].endswith("/media") for p in posts)
        assert any(p["url"].endswith("/interactive") for p in posts)
        assert "Opciones enviadas" in out

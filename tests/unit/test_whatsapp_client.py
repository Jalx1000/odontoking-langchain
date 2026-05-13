"""Unit tests for the WhatsApp Cloud API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class TestBuildInteractivePayload:
    """Tests for build_interactive_payload — message format detection."""

    def _build(self, text: str):
        from app.services.whatsapp_client import build_interactive_payload

        return build_interactive_payload(text, "591700000000")

    def test_plain_text_returns_none(self):
        assert self._build("Hola, ¿cómo estás?") is None

    def test_single_option_returns_none(self):
        msg = "Elige una opción:\n1) Ortodoncia"
        assert self._build(msg) is None

    def test_two_options_returns_button_type(self):
        msg = "Elige tu seguro:\n1) Alianza\n2) Nacional Vida"
        result = self._build(msg)
        assert result is not None
        assert result["type"] == "button"
        buttons = result["action"]["buttons"]
        assert len(buttons) == 2
        assert buttons[0]["reply"]["title"] == "Alianza"
        assert buttons[1]["reply"]["title"] == "Nacional Vida"

    def test_three_options_returns_button_type(self):
        msg = "Opciones:\n1) A\n2) B\n3) C"
        result = self._build(msg)
        assert result is not None
        assert result["type"] == "button"

    def test_four_or_more_options_returns_list_type(self):
        msg = "Servicios:\n1) Ortodoncia\n2) Blanqueamiento\n3) Implantes\n4) Limpieza"
        result = self._build(msg)
        assert result is not None
        assert result["type"] == "list"
        rows = result["action"]["sections"][0]["rows"]
        assert len(rows) == 4

    def test_list_rows_capped_at_ten(self):
        options = "\n".join(f"{i}) Option {i}" for i in range(1, 15))
        result = self._build(f"Many options:\n{options}")
        assert result is not None
        rows = result["action"]["sections"][0]["rows"]
        assert len(rows) <= 10

    def test_title_truncated_to_24_chars_for_list(self):
        long_title = "A" * 30
        msg = f"Choose:\n1) {long_title}\n2) Short\n3) Another\n4) Fourth"
        result = self._build(msg)
        rows = result["action"]["sections"][0]["rows"]
        assert len(rows[0]["title"]) <= 24

    def test_button_title_truncated_to_20_chars(self):
        msg = "Choose:\n1) VeryLongOptionNameHere\n2) Also Long Option Name"
        result = self._build(msg)
        buttons = result["action"]["buttons"]
        for btn in buttons:
            assert len(btn["reply"]["title"]) <= 20

    def test_body_text_not_included_in_options(self):
        msg = "¿Cuál seguro tiene?\n1) Alianza\n2) Nacional Vida"
        result = self._build(msg)
        assert "¿Cuál seguro tiene?" in result["body"]["text"]

    def test_body_text_truncated_to_1024(self):
        long_body = "X" * 2000
        msg = f"{long_body}\n1) Option1\n2) Option2"
        result = self._build(msg)
        assert len(result["body"]["text"]) <= 1024


class TestSendTextMessage:
    @pytest.mark.asyncio
    async def test_send_text_message_success(self):
        from app.services.whatsapp_client import send_text_message

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"messages": [{"id": "wamid.abc"}]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_text_message("591700000000", "Hola")
            assert result == {"messages": [{"id": "wamid.abc"}]}

    @pytest.mark.asyncio
    async def test_send_text_raises_on_http_error(self):
        from app.services.whatsapp_client import send_text_message

        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_response
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await send_text_message("591700000000", "Hola")


class TestTranscribeAudio:
    @pytest.mark.asyncio
    async def test_transcribe_audio_success(self):
        from app.services.whatsapp_client import transcribe_audio

        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "Hola quiero un turno"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await transcribe_audio(b"fake-audio-bytes", "audio/ogg")
            assert result == "Hola quiero un turno"

    @pytest.mark.asyncio
    async def test_transcribe_audio_returns_empty_on_failure(self):
        from app.services.whatsapp_client import transcribe_audio

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("network error"))
            mock_client_cls.return_value = mock_client

            result = await transcribe_audio(b"bytes", "audio/ogg")
            assert result == ""

    @pytest.mark.asyncio
    async def test_transcribe_uses_correct_extension(self):
        from app.services.whatsapp_client import transcribe_audio

        posted_files = {}

        async def capture_post(url, **kwargs):
            posted_files.update(kwargs.get("files", {}))
            m = MagicMock()
            m.json.return_value = {"text": "ok"}
            m.raise_for_status = MagicMock()
            return m

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_cls.return_value = mock_client

            await transcribe_audio(b"bytes", "audio/mpeg")
            filename, _, _ = posted_files["file"]
            assert filename == "audio.mp3"

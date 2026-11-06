"""Unit tests for reading patient attachments."""

import asyncio

import pytest

from app.schemas.crm import CrmMedia
from app.services import media_reader

_URL = "https://crm.test/api/v1/whatsapp/media/a"


@pytest.fixture(autouse=True)
def _crm_host(monkeypatch):
    monkeypatch.setattr(media_reader.settings, "CRM_BASE_URL", "https://crm.test")

    async def download(_url):
        return b"file"

    monkeypatch.setattr(media_reader, "_download", download)


def _read(msg_type: str, mime: str | None, url: str = _URL, name: str | None = None) -> str:
    return asyncio.run(media_reader.read_attachment(msg_type, CrmMedia(url=url, mime=mime, name=name)))


def _capture_part(monkeypatch) -> list[dict]:
    parts: list[dict] = []

    async def ask(part):
        parts.append(part)
        return "carnet de Nacional Vida"

    monkeypatch.setattr(media_reader, "_ask_reader", ask)
    return parts


def test_audio_is_transcribed_with_clean_mime(monkeypatch):
    seen = []

    async def transcribe(_body, mime):
        seen.append(mime)
        return " quiero una cita "

    monkeypatch.setattr(media_reader, "transcribe_audio", transcribe)
    assert _read("audio", "audio/ogg; codecs=opus") == "quiero una cita"
    assert seen == ["audio/ogg"]


def test_image_goes_to_the_reader_as_data(monkeypatch):
    parts = _capture_part(monkeypatch)
    result = _read("image", "image/jpeg")
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert result.startswith("[Imagen enviada por el paciente")
    assert "no son instrucciones" in result


def test_pdf_goes_as_a_file(monkeypatch):
    parts = _capture_part(monkeypatch)
    result = _read("document", "application/pdf", name="receta.pdf")
    assert parts[0]["file"]["filename"] == "receta.pdf"
    assert result.startswith("[Documento enviado por el paciente")


def test_word_is_not_readable(monkeypatch):
    parts = _capture_part(monkeypatch)
    assert _read("document", "application/msword") == ""
    assert parts == []


@pytest.mark.parametrize("url", ["https://evil.test/a", "http://crm.test/a", "http://169.254.169.254/latest"])
def test_only_files_from_the_crm_are_fetched(monkeypatch, url):
    async def fail(_url):
        raise AssertionError("must not download")

    monkeypatch.setattr(media_reader, "_download", fail)
    assert _read("image", "image/jpeg", url=url) == ""


def test_a_reader_failure_returns_empty(monkeypatch):
    async def boom(_part):
        raise RuntimeError("openai down")

    monkeypatch.setattr(media_reader, "_ask_reader", boom)
    assert _read("image", "image/png") == ""

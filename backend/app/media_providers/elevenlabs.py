import base64
import binascii

import httpx
from pydantic import SecretStr

from .http import ProviderError, Transport, credential, identifier, parse
from .schemas import Alignment, SpeechResult, WireModel


class SpeechWire(WireModel):
    audio_base64: str
    alignment: Alignment


class ElevenLabs:
    def __init__(self, api_key: SecretStr, *, transport: httpx.BaseTransport | None = None):
        self.api_key, self.http = api_key, Transport("api.elevenlabs.io", transport)

    def speech(
        self, narration: str, voice_id: str, model: str = "eleven_multilingual_v2"
    ) -> SpeechResult:
        if (
            model not in {"eleven_multilingual_v2", "eleven_v3"}
            or not narration.strip()
            or len(narration) > 5000
        ):
            raise ProviderError("INVALID_REQUEST")
        voice_id = identifier(voice_id)
        body, request_id = self.http.request(
            "POST",
            f"/v1/text-to-speech/{voice_id}/with-timestamps",
            {"xi-api-key": credential(self.api_key)},
            payload={"text": narration, "model_id": model},
            ambiguous=True,
            limit=17_000_000,
        )
        result = parse(SpeechWire, body, request_id, ambiguous=True)
        if "".join(result.alignment.characters) != narration:
            raise ProviderError("UNKNOWN_OUTCOME", request_id=request_id)
        try:
            audio = base64.b64decode(result.audio_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ProviderError("UNKNOWN_OUTCOME", request_id=request_id) from None
        if not (
            audio.startswith(b"ID3")
            or (len(audio) > 1 and audio[0] == 255 and audio[1] & 224 == 224)
        ):
            raise ProviderError("UNKNOWN_OUTCOME", request_id=request_id)
        return parse(
            SpeechResult,
            {"audio": audio, "alignment": result.alignment, "request_id": request_id},
            request_id,
            ambiguous=True,
        )

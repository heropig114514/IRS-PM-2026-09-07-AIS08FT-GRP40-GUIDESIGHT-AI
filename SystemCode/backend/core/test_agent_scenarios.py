"""Responsibilities: Expand data-driven functional coverage for the Agent service boundaries.
Implementation: Exercise place search, POI discovery, Qwen completion/ASR and realtime TTS with
bounded in-memory HTTP/WebSocket fixtures. Each scenario preserves the production validation,
serialization and error-classification path without contacting an external provider.
Related Modules: core.agent.places, core.agent.poi, core.agent.provider, core.agent.tts;
core.test_agent_tts supplies reusable provider protocol doubles.
Declaration Index: wav_fixture; search_feature; poi_feature; ScenarioTests;
ScenarioTests.test_search_configuration_and_input_matrix;
ScenarioTests.test_search_provider_response_matrix;
ScenarioTests.test_poi_provider_and_catalog_matrix;
ScenarioTests.test_provider_configuration_and_completion_matrix;
ScenarioTests.test_transcription_matrix;
ScenarioTests.test_tts_protocol_matrix.
Variable Index: HTTP_FIXTURE_KEY — synthetic authorization value used only by request doubles;
VALID_AGENT_CONFIG — bounded Qwen configuration fixture; VALID_TTS_CONFIG — bounded TTS fixture.
"""

import base64
import io
import json
import os
import wave
from unittest.mock import AsyncMock, patch

import httpx2
from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, override_settings

from core.agent import places, poi, provider, tts
from core.agent.provider import AgentError
from core.test_agent_tts import FakeConnector, FakePeer

HTTP_FIXTURE_KEY = "scenario-fixture-key"
VALID_AGENT_CONFIG = {
    "key": HTTP_FIXTURE_KEY,
    "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "model": "qwen3.6-plus",
    "asr_model": "qwen3-asr-flash",
}
VALID_TTS_CONFIG = {"key": HTTP_FIXTURE_KEY, "url": VALID_AGENT_CONFIG["url"]}


def wav_fixture(*, frames=1600, channels=1, width=2, rate=16000, truncate=False):
    """Functionality: Build a deterministic PCM WAV input for ASR validation.
    Inputs: frame count, channel count, sample width, sample rate and optional truncation.
    Outputs: Base64-encoded WAV bytes. Logic: Use the standard wave writer so headers match browser
    output; Constraints: test audio is silence and does not establish recognition quality.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b"\x00" * frames * channels * width)
    data = buffer.getvalue()
    if truncate:
        data = data[:-2]
    return base64.b64encode(data).decode()


def search_feature(*, country="SGP", label="Scenario store", metadata=None):
    """Functionality: Construct one valid or deliberately modified Pelias feature.
    Inputs: country code, display label and optional property overrides. Outputs: JSON-compatible
    Point feature. Logic: Keep the normal geometry/provenance shape explicit for matrix mutations.
    Constraints: Coordinates are synthetic Singapore fixture data, not a place assertion.
    """
    properties = {"label": label, "country_a": country, "name": "Scenario store",
                  "layer": "venue", "accuracy": "point"}
    if metadata:
        properties.update(metadata)
    return {"geometry": {"type": "Point", "coordinates": [103.77, 1.30]},
            "properties": properties}


def poi_feature(*, name="Scenario shop", distance=80, category_ids=None):
    """Functionality: Construct one provider POI feature for nearby-place cases.
    Inputs: optional name, straight-line distance and category mapping. Outputs: feature data.
    Logic: Use the exact provider fields consumed by the adapter. Constraints: No real shop claim.
    """
    return {"geometry": {"type": "Point", "coordinates": [103.771, 1.301]},
            "properties": {"distance": distance,
                           "category_ids": category_ids or {"90001": {}},
                           "osm_tags": {} if name is None else {"name": name}}}


class ScenarioTests(SimpleTestCase):
    """Functionality: Run broad deterministic boundary matrices for Agent integrations.
    Logic: Reuse production adapters and classify each failure by its public stable code.
    Constraints: Provider responses, locations, speech and audio are synthetic test fixtures.
    """

    async def test_search_configuration_and_input_matrix(self):
        """Functionality: Validate deployment search settings and request boundaries.
        Inputs: valid, malformed and missing country/language/key/focus/query combinations.
        Outputs: stable AgentError codes before external I/O. Logic: table-driven subtests.
        Constraints: No provider client is allowed to open for preflight failures.
        """
        for environment, code in (
            ({"PRS_AGENT_SEARCH_COUNTRY": "sgp"}, "search_not_configured"),
            ({"PRS_AGENT_SEARCH_LANGUAGE": "EN"}, "search_not_configured"),
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=False
            ):
                with self.assertRaises(AgentError) as caught:
                    places.search_context()
                self.assertEqual(caught.exception.code, code)

        with patch.dict(os.environ, {
            "PRS_AGENT_SEARCH_COUNTRY": "MYS", "PRS_AGENT_SEARCH_LANGUAGE": "ms",
        }, clear=False):
            self.assertEqual(places.search_context()["default_search_country"], "MYS")
            self.assertEqual(places.search_context()["query_language"], "ms")

        invalid_requests = [
            (None, None, None, "invalid_place"),
            ("", None, None, "invalid_place"),
            ("x" * 201, None, None, "invalid_place"),
            ("Library", [200, 1], None, "invalid_focus"),
            ("Library", None, "Singapore", "invalid_country"),
        ]
        with patch("core.agent.places.httpx2.AsyncClient") as client:
            for text, focus, country, code in invalid_requests:
                with self.subTest(text_type=type(text).__name__, code=code):
                    with self.assertRaises(AgentError) as caught:
                        await places.search_places(text, focus=focus, country=country)
                    self.assertEqual(caught.exception.code, code)
            client.assert_not_called()

        with patch.dict(os.environ, {"ORS_API_KEY": ""}, clear=False):
            with self.assertRaises(AgentError) as caught:
                await places.search_places("Library")
        self.assertEqual(caught.exception.code, "ors_not_configured")

    async def test_search_provider_response_matrix(self):
        """Functionality: Preserve explicit geocoder response and transport classifications.
        Inputs: successful mixed-country data, status errors, oversized, malformed and invalid
        feature payloads plus a network exception. Outputs: candidates or stable AgentError codes.
        Logic: One MockTransport per case; no retry is permitted. Constraints: no external traffic.
        """
        cases = [
            ("status", lambda request: httpx2.Response(429), "geocoder_http_error"),
            ("json", lambda request: httpx2.Response(200, content=b"not-json"),
             "geocoder_invalid_response"),
            ("geometry", lambda request: httpx2.Response(200, json={"features": [
                {"geometry": {"type": "LineString", "coordinates": []},
                 "properties": {"label": "bad", "country_a": "SGP"}},
            ]}), "geocoder_invalid_response"),
            ("label", lambda request: httpx2.Response(200, json={"features": [
                search_feature(label=""),
            ]}), "geocoder_invalid_response"),
            ("metadata", lambda request: httpx2.Response(200, json={"features": [
                search_feature(metadata={"name": 12}),
            ]}), "geocoder_invalid_response"),
            ("oversized", lambda request: httpx2.Response(200, content=b"x" * 262145),
             "geocoder_response_limit"),
            ("network", lambda request: (_ for _ in ()).throw(
                httpx2.ConnectError("synthetic network failure", request=request)
            ), "geocoder_network_error"),
        ]
        for name, handler, code in cases:
            with self.subTest(case=name):
                client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
                with patch.dict(os.environ, {"ORS_API_KEY": HTTP_FIXTURE_KEY}, clear=False), \
                        patch("core.agent.places.httpx2.AsyncClient", return_value=client):
                    with self.assertRaises(AgentError) as caught:
                        await places.search_places("scenario store", focus=[103.77, 1.30])
                self.assertEqual(caught.exception.code, code)

    async def test_poi_provider_and_catalog_matrix(self):
        """Functionality: Cover POI request, category catalog and nearby result boundaries.
        Inputs: empty/duplicate/invalid catalog rows, HTTP/network/size failures and malformed
        nearby features. Outputs: stable classifications and one valid unnamed candidate.
        Logic: Matrix fixtures reach the real request/parser layers exactly once per case.
        Constraints: Distances and category IDs are synthetic provider data.
        """
        request_cases = [
            ("missing-key", {}, "ors_not_configured"),
            ("status", {"status_code": 403}, "poi_http_error"),
            ("json", {"status_code": 200, "content": b"not-json"},
             "poi_invalid_response"),
            ("oversized", {"status_code": 200, "content": b"x" * 524289},
             "poi_response_limit"),
        ]
        for name, response, code in request_cases:
            with self.subTest(request=name):
                if name == "missing-key":
                    with patch.dict(os.environ, {"ORS_API_KEY": ""}, clear=False):
                        with self.assertRaises(AgentError) as caught:
                            await poi.request_pois({"request": "list"})
                else:
                    client = httpx2.AsyncClient(transport=httpx2.MockTransport(
                        lambda request, response=response: httpx2.Response(**response)
                    ))
                    with patch.dict(os.environ, {"ORS_API_KEY": HTTP_FIXTURE_KEY}, clear=False), \
                            patch("core.agent.poi.httpx2.AsyncClient", return_value=client):
                        with self.assertRaises(AgentError) as caught:
                            await poi.request_pois({"request": "list"})
                self.assertEqual(caught.exception.code, code)

        catalog_cases = [
            ("empty", {"group": {"children": {}}}),
            ("invalid-id", {"group": {"children": {"shop": {"x": 0}}}}),
            ("duplicate", {"group": {"children": {"shop": {"a": 90001, "b": 90001}}}}),
        ]
        for name, payload in catalog_cases:
            with self.subTest(catalog=name), patch.dict(
                os.environ, {"ORS_API_KEY": HTTP_FIXTURE_KEY}, clear=False
            ), patch("core.agent.poi.request_pois", AsyncMock(return_value=payload)):
                if name == "duplicate":
                    result = await poi.categories()
                    self.assertEqual(
                        result["categories"],
                        [{"id": 90001, "name": "a", "group": "group"}],
                    )
                else:
                    with self.assertRaises(AgentError) as caught:
                        await poi.categories()
                    self.assertEqual(caught.exception.code, "poi_invalid_catalog")

        nearby_cases = [
            ("too-many", {"features": [poi_feature()] * 6}),
            ("geometry", {"features": [{**poi_feature(), "geometry": {"type": "LineString"}}]}),
            ("distance", {"features": [poi_feature(distance=-1)]}),
            ("category", {"features": [poi_feature(category_ids={"99999": {}})]}),
            ("name", {"features": [poi_feature(name="x" * 501)]}),
        ]
        for name, payload in nearby_cases:
            with self.subTest(nearby=name), patch(
                "core.agent.poi.request_pois", AsyncMock(return_value=payload)
            ):
                with self.assertRaises(AgentError) as caught:
                    await poi.nearby([103.77, 1.30], [90001])
                self.assertEqual(caught.exception.code, "poi_invalid_response")

        with patch("core.agent.poi.request_pois", AsyncMock(return_value={
            "features": [poi_feature(name=None, distance=100)],
        })):
            result = await poi.nearby([103.77, 1.30], [90001])
        self.assertEqual(result["candidates"][0]["label"], "Unnamed place")

    async def test_provider_configuration_and_completion_matrix(self):
        """Functionality: Exercise Qwen configuration, context, transport and message contracts.
        Inputs: configuration mutations and deterministic HTTP responses for text, tools and ASR.
        Outputs: normalized responses or typed failures. Logic: use real request serialization with
        MockTransport and assert the provider boundary is called once. Constraints: no Qwen access.
        """
        invalid_configs = [
            {"PRS_AGENT_API_KEY": "", "PRS_AGENT_BASE_URL": VALID_AGENT_CONFIG["url"]},
            {"PRS_AGENT_API_KEY": HTTP_FIXTURE_KEY, "PRS_AGENT_BASE_URL": "https://invalid"},
            {"PRS_AGENT_API_KEY": HTTP_FIXTURE_KEY, "PRS_AGENT_BASE_URL": VALID_AGENT_CONFIG["url"],
             "PRS_AGENT_MODEL": ""},
        ]
        for environment in invalid_configs:
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=False
            ):
                with self.assertRaises(AgentError) as caught:
                    provider.configuration()
                self.assertEqual(caught.exception.code, "agent_not_configured")
        with patch.dict(os.environ, {
            "PRS_AGENT_API_KEY": HTTP_FIXTURE_KEY, "PRS_AGENT_BASE_URL": VALID_AGENT_CONFIG["url"],
            "PRS_AGENT_MODEL": "scenario-model", "PRS_ASR_MODEL": "scenario-asr",
        }, clear=False):
            self.assertEqual(provider.configuration()["model"], "scenario-model")

        with self.assertRaises(AgentError) as caught:
            provider.enforce_context_limit({"messages": ["x" * provider.MAX_CONTEXT_CHARS]})
        self.assertEqual(caught.exception.code, "context_limit")

        response_cases = [
            ("status", lambda request: httpx2.Response(503), "qwen_http_error"),
            ("json", lambda request: httpx2.Response(200, content=b"{}"), "qwen_invalid_response"),
            ("role", lambda request: httpx2.Response(200, json={"choices": [{"message": {
                "role": "user", "content": "bad"}}]}), "qwen_invalid_response"),
            ("empty", lambda request: httpx2.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": " "}}]}), "qwen_empty_response"),
            ("oversized", lambda request: httpx2.Response(200, content=b"x" * 262145),
             "qwen_response_limit"),
            ("network", lambda request: (_ for _ in ()).throw(
                httpx2.ConnectError("synthetic network failure", request=request)
            ), "qwen_network_error"),
        ]
        for name, handler, code in response_cases:
            with self.subTest(completion=name):
                client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
                with patch("core.agent.provider.configuration", return_value=VALID_AGENT_CONFIG), \
                        patch("core.agent.provider.httpx2.AsyncClient", return_value=client):
                    with self.assertRaises(AgentError) as caught:
                        await provider.completion([{"role": "user", "content": "scenario"}])
                self.assertEqual(caught.exception.code, code)

        requests = []
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(
            lambda request: requests.append(request) or httpx2.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            })
        ))
        with patch("core.agent.provider.configuration", return_value=VALID_AGENT_CONFIG), \
                patch("core.agent.provider.httpx2.AsyncClient", return_value=client):
            result = await provider.completion([{"role": "user", "content": "audio"}], audio=True)
        self.assertEqual(result["content"], "ok")
        self.assertEqual(json.loads(requests[0].content)["model"], VALID_AGENT_CONFIG["asr_model"])

    async def test_transcription_matrix(self):
        """Functionality: Cover WAV metadata, duration, truncation and transcript contracts.
        Inputs: valid silence plus malformed encodings, formats and provider messages. Outputs:
        one transcript or stable AgentError. Logic: preflight all invalid audio before completion.
        Constraints: Silence is a transport fixture, not speech recognition evidence.
        """
        invalid_audio = [
            None, "not-base64", wav_fixture(channels=2), wav_fixture(rate=8000),
            wav_fixture(frames=800), wav_fixture(frames=20 * 16000 + 1), wav_fixture(truncate=True),
        ]
        with patch("core.agent.provider.completion", AsyncMock()) as completion:
            for encoded in invalid_audio:
                with self.subTest(
                    audio_type=type(encoded).__name__, audio_length=len(encoded or "")
                ):
                    with self.assertRaises(AgentError) as caught:
                        await provider.transcribe(encoded)
                    self.assertEqual(caught.exception.code, "invalid_audio")
            completion.assert_not_awaited()

        for message in ({"content": " "}, {"content": "x" * 2001},
                        {"content": "tool", "tool_calls": [{"id": "x"}]}):
            with self.subTest(message_keys=tuple(message)):
                with patch("core.agent.provider.completion", AsyncMock(return_value=message)):
                    with self.assertRaises(AgentError) as caught:
                        await provider.transcribe(wav_fixture())
                self.assertEqual(caught.exception.code, "invalid_transcript")

    async def test_tts_protocol_matrix(self):
        """Functionality: Cover TTS upstream protocol, provider and socket admission failures.
        Inputs: malformed handshake/update/delta/error events and configuration/disconnect cases.
        Outputs: stable AgentError codes and ASGI close/error messages. Logic: one fake peer per
        case.
        Constraints: No provider connection, audio output or audibility claim.
        """
        request = tts.validate_request(json.dumps({
            "type": "synthesize", "request_id": "00000000-0000-4000-8000-000000000001",
            "text": "scenario", "language": "en-US", "rate": 1,
        }))
        protocol_cases = [
            ([{"type": "wrong"}], "tts_upstream_protocol"),
            ([{"type": "session.created"}, {"type": "wrong"}], "tts_upstream_protocol"),
            ([{"type": "session.created"}, {"type": "session.updated"},
              {"type": "error"}], "tts_upstream_error"),
            ([{"type": "session.created"}, {"type": "session.updated"},
              {"type": "response.audio.delta", "delta": "%%%"}], "tts_upstream_protocol"),
            ([{"type": "session.created"}, {"type": "session.updated"},
              {"type": "response.audio.delta", "delta": base64.b64encode(b"x").decode()}],
             "tts_upstream_protocol"),
        ]
        for events, code in protocol_cases:
            with self.subTest(code=code, events=len(events)):
                peer = FakePeer(events)
                with self.assertRaises(AgentError) as caught:
                    await tts.upstream_session(request, VALID_TTS_CONFIG, AsyncMock(),
                                                opener=FakeConnector(peer))
                self.assertEqual(caught.exception.code, code)

        with self.assertRaises(AgentError) as caught:
            tts.upstream_url({"key": "", "url": VALID_TTS_CONFIG["url"]})
        self.assertEqual(caught.exception.code, "tts_not_configured")

        @override_settings(CAMERA_ALLOWED_ORIGINS={"http://127.0.0.1:8766"})
        async def missing_config_socket():
            scope = {"type": "websocket", "path": "/ws/tts/", "headers": [
                (b"origin", b"http://127.0.0.1:8766"), (b"host", b"127.0.0.1:8766")
            ]}
            app = ApplicationCommunicator(tts.tts_socket, scope)
            await app.send_input({"type": "websocket.connect"})
            await app.receive_output()
            await app.send_input({"type": "websocket.receive", "text": json.dumps({
                "type": "synthesize", "request_id": "00000000-0000-4000-8000-000000000001",
                "text": "scenario", "language": "en-US", "rate": 1,
            })})
            error = json.loads((await app.receive_output())["text"])
            close = await app.receive_output()
            await app.wait()
            return error, close

        with patch("core.agent.tts.configuration", side_effect=AgentError(
            "agent_not_configured", "fixture"
        )):
            error, close = await missing_config_socket()
        self.assertEqual(error["code"], "tts_not_configured")
        self.assertEqual(close["code"], 1011)

"""
Unit / integration tests for the PureImageAI Flask application.

Run with:
    pytest tests/test_app.py -v
"""

import hashlib
import json
import os
import sys
import time
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure app can be imported without real provider keys configured
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as application  # noqa: E402  (after sys.path tweak)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client():
    application.app.config["TESTING"] = True
    return application.app.test_client()


def _post_json(client, path, data):
    return client.post(
        path,
        data=json.dumps(data),
        content_type="application/json",
    )


# ===========================================================================
# Cache helpers
# ===========================================================================

class TestCacheHelpers(unittest.TestCase):
    """The LLM response cache (_cache_get / _cache_set) must not be shadowed
    by the generate image cache (_gen_cache_get / _gen_cache_set)."""

    def test_resp_cache_miss_returns_none(self):
        result = application._cache_get("not_in_cache")
        self.assertIsNone(result)

    def test_resp_cache_roundtrip(self):
        application._cache_set("llm_key_1", "enhanced prompt text")
        result = application._cache_get("llm_key_1")
        self.assertEqual(result, "enhanced prompt text")
        # clean up
        with application._resp_cache_lock:
            application._resp_cache.pop("llm_key_1", None)

    def test_gen_cache_miss_returns_none_none(self):
        urls, provider = application._gen_cache_get("not_in_gen_cache")
        self.assertIsNone(urls)
        self.assertIsNone(provider)

    def test_gen_cache_roundtrip(self):
        application._gen_cache_set("gen_key_1", ["http://example.com/a.png"], "pollinations")
        urls, provider = application._gen_cache_get("gen_key_1")
        self.assertEqual(urls, ["http://example.com/a.png"])
        self.assertEqual(provider, "pollinations")
        # clean up
        with application._GENERATE_CACHE_LOCK:
            application._GENERATE_CACHE.pop("gen_key_1", None)

    def test_cache_types_are_distinct(self):
        """LLM cache and generate cache must be completely independent objects."""
        self.assertIsNot(application._resp_cache, application._GENERATE_CACHE)


# ===========================================================================
# /enhance_prompt endpoint
# ===========================================================================

class TestEnhancePromptEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    def test_missing_prompt_returns_400(self):
        resp = _post_json(self.client, "/enhance_prompt", {})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)
        self.assertIn("prompt", data["error"].lower())

    def test_empty_prompt_returns_400(self):
        resp = _post_json(self.client, "/enhance_prompt", {"prompt": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_prompt_too_long_returns_400(self):
        resp = _post_json(self.client, "/enhance_prompt", {"prompt": "x" * 4001})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_no_llm_key_returns_503(self):
        """When no LLM API keys are present the endpoint must return 503
        with an actionable message — NOT a generic 500."""
        with patch.object(application, "_has_llm_key", return_value=False):
            resp = _post_json(self.client, "/enhance_prompt", {"prompt": "a sunset"})
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertIn("error", data)
        # message should guide the user to configure a key
        self.assertIn("key", data["error"].lower())

    def test_llm_success_returns_enhanced(self):
        """When an LLM provider returns text the endpoint must return it
        as {"enhanced": "..."}."""
        expected = "A stunning golden sunset over the ocean with vibrant orange hues"
        with patch.object(application, "_has_llm_key", return_value=True), \
             patch.object(application, "llm", return_value=expected):
            resp = _post_json(self.client, "/enhance_prompt", {"prompt": "a sunset"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("enhanced"), expected)

    def test_llm_failure_returns_502(self):
        """When all LLM providers fail (llm() returns None) the endpoint must
        return 502 — NOT a generic 500."""
        with patch.object(application, "_has_llm_key", return_value=True), \
             patch.object(application, "llm", return_value=None):
            resp = _post_json(self.client, "/enhance_prompt", {"prompt": "a sunset"})
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_llm_bad_return_type_returns_500(self):
        """If llm() somehow returns a non-string (tuple etc.), the endpoint
        must catch it and return 500 — not crash Flask."""
        with patch.object(application, "_has_llm_key", return_value=True), \
             patch.object(application, "llm", return_value=(None, None)):
            resp = _post_json(self.client, "/enhance_prompt", {"prompt": "a sunset"})
        self.assertEqual(resp.status_code, 500)


# ===========================================================================
# /generate endpoint
# ===========================================================================

class TestGenerateEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    def test_missing_prompt_returns_400(self):
        resp = _post_json(self.client, "/generate", {})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_empty_prompt_returns_400(self):
        resp = _post_json(self.client, "/generate", {"prompt": ""})
        self.assertEqual(resp.status_code, 400)

    def test_no_providers_returns_503_with_guidance(self):
        """When ALL provider keys are absent the response must be 503 with
        an actionable message — NOT the generic 'temporarily unavailable'."""
        with patch.object(application, "_generate_images", return_value=(None, None)), \
             patch.dict(os.environ, {}, clear=False), \
             patch.object(application, "FAL_KEY", None), \
             patch.object(application, "HF_KEY", None), \
             patch.object(application, "STABILITY_KEY", None), \
             patch.object(application, "REPLICATE_KEY", None):
            resp = _post_json(self.client, "/generate", {"prompt": "a cat"})
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertIn("error", data)
        # Should mention at least one key name
        self.assertTrue(
            any(k in data["error"] for k in ("FAL_KEY", "HF_KEY", "STABILITY_KEY", "REPLICATE_KEY")),
            msg=f"Error message should name a key: {data['error']}"
        )

    def test_provider_failure_with_keys_returns_502(self):
        """When at least one key is configured but all providers fail, return 502."""
        with patch.object(application, "_generate_images", return_value=(None, None)), \
             patch.object(application, "FAL_KEY", "some-key"):
            resp = _post_json(self.client, "/generate", {"prompt": "a cat"})
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_successful_generation_returns_images(self):
        """Happy path: _generate_images returns URLs, endpoint returns them."""
        fake_urls = ["http://example.com/img1.png", "http://example.com/img2.png"]
        with patch.object(application, "_generate_images", return_value=(fake_urls, "pollinations")), \
             patch.object(application, "_check_gen_rate_limit", return_value=True):
            resp = _post_json(self.client, "/generate", {"prompt": "a cat"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("images", data)
        returned_urls = [img["url"] for img in data["images"]]
        self.assertEqual(returned_urls, fake_urls)
        self.assertIn("elapsed_ms", data)

    def test_response_shape(self):
        """Success response must include images list and elapsed_ms."""
        fake_urls = ["data:image/png;base64,abc"]
        with patch.object(application, "_generate_images", return_value=(fake_urls, "pollinations")), \
             patch.object(application, "_check_gen_rate_limit", return_value=True):
            resp = _post_json(self.client, "/generate", {
                "prompt": "a dog",
                "style": "photorealistic",
                "aspect_ratio": "landscape",
                "num_images": 1,
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data["images"], list)
        self.assertIsInstance(data["elapsed_ms"], int)


# ===========================================================================
# /health endpoint
# ===========================================================================

class TestHealthEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "ok")


# ===========================================================================
# llm() helper
# ===========================================================================

class TestLlmHelper(unittest.TestCase):
    """Verify that llm() returns None (not a tuple) when all providers fail."""

    def test_llm_returns_none_when_no_keys(self):
        result = application.llm("system", "user")
        self.assertIsNone(result)

    def test_llm_returns_string_on_success(self):
        def _fake_llm(system, user):
            return "enhanced text"

        original = application._LLM_PROVIDERS[:]
        application._LLM_PROVIDERS[:] = [("fake", _fake_llm)]
        try:
            result = application.llm("system", "user")
            # Clear any cached entry we just wrote
            cache_key = hashlib.md5(("system" + "user").encode()).hexdigest()
            with application._resp_cache_lock:
                application._resp_cache.pop(cache_key, None)
        finally:
            application._LLM_PROVIDERS[:] = original

        self.assertIsInstance(result, str)
        self.assertEqual(result, "enhanced text")


# ===========================================================================
# /proxy_image endpoint
# ===========================================================================

class TestProxyImageEndpoint(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    def test_missing_url_returns_400(self):
        resp = self.client.get("/proxy_image")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_disallowed_url_returns_400(self):
        resp = self.client.get("/proxy_image?url=http://evil.com/bad.png")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_disallowed_scheme_returns_400(self):
        resp = self.client.get("/proxy_image?url=file:///etc/passwd")
        self.assertEqual(resp.status_code, 400)

    def test_ssrf_userinfo_bypass_rejected(self):
        """Userinfo-in-URL SSRF bypass must be rejected.

        The URL ``http://fal.run:80@evil.com/image.png`` is parsed as:
          - userinfo = ``fal.run:80`` (username:password)
          - host     = ``evil.com``

        The old ``netloc.split(":")[0]`` code extracted ``fal.run`` (the
        username), which matched the allowlist and let the request through.
        The fixed code uses ``parsed.hostname`` which correctly returns
        ``evil.com``, which is not in the allowlist.
        """
        url = "http://fal.run:80@evil.com/image.png"
        resp = self.client.get(f"/proxy_image?url={url}")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_allowed_url_returns_image(self):
        """An allowed upstream URL proxied successfully returns image bytes."""
        fake_image = b"\x89PNG\r\n"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/png"}
        mock_resp.content = fake_image
        with patch("requests.get", return_value=mock_resp):
            resp = self.client.get(
                "/proxy_image?url=https://image.pollinations.ai/prompt/test"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("image/", resp.content_type)
        self.assertEqual(resp.data, fake_image)


# ===========================================================================
# Rate limiting — GET routes must be exempt
# ===========================================================================

class TestRateLimitingGetExemption(unittest.TestCase):
    """GET routes must never be blocked by the POST-only global rate limiter."""

    def setUp(self):
        self.client = _client()

    def test_health_never_rate_limited(self):
        # Even if the rate limiter would reject, GET /health must return 200.
        with patch.object(application, "_check_global_rate_limit", return_value=False):
            resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_debug_never_rate_limited(self):
        with patch.object(application, "_check_global_rate_limit", return_value=False):
            resp = self.client.get("/debug")
        self.assertEqual(resp.status_code, 200)

    def test_index_never_rate_limited(self):
        with patch.object(application, "_check_global_rate_limit", return_value=False):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_post_rate_limited_returns_429_with_request_id(self):
        """A rate-limited POST must return 429 with a request_id field."""
        with patch.object(application, "_check_global_rate_limit", return_value=False):
            resp = _post_json(self.client, "/generate", {"prompt": "test"})
        self.assertEqual(resp.status_code, 429)
        data = resp.get_json()
        self.assertIn("error", data)
        self.assertIn("request_id", data)


# ===========================================================================
# request_id propagation
# ===========================================================================

class TestRequestIdPropagation(unittest.TestCase):
    """Unhandled exceptions (500) must always include request_id in the response."""

    def setUp(self):
        self.client = _client()

    def test_500_includes_request_id(self):
        """When generate() raises unexpectedly, the 500 response includes request_id."""
        with patch.object(application, "_check_gen_rate_limit", side_effect=RuntimeError("boom")):
            resp = _post_json(self.client, "/generate", {"prompt": "a cat"})
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertIn("request_id", data)
        self.assertIsInstance(data["request_id"], str)
        self.assertGreater(len(data["request_id"]), 0)

    def test_enhance_prompt_500_includes_request_id(self):
        """When enhance_prompt() raises unexpectedly, the 500 includes request_id."""
        with patch.object(application, "_has_llm_key", side_effect=RuntimeError("boom")):
            resp = _post_json(self.client, "/enhance_prompt", {"prompt": "a sunset"})
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertIn("request_id", data)

    def test_gen_rate_limit_429_includes_request_id(self):
        """Per-endpoint 429 from _check_gen_rate_limit must include request_id."""
        with patch.object(application, "_check_gen_rate_limit", return_value=False):
            resp = _post_json(self.client, "/generate", {"prompt": "a cat"})
        self.assertEqual(resp.status_code, 429)
        data = resp.get_json()
        self.assertIn("request_id", data)


# ===========================================================================
# Cache TTL behavior
# ===========================================================================

class TestCacheTTL(unittest.TestCase):
    """Cache entries must be treated as expired once the TTL has passed."""

    def test_llm_cache_entry_expires(self):
        """An LLM cache entry older than _CACHE_TTL must not be returned."""
        key = "ttl_test_llm_key"
        # Write the entry while time is "now"
        application._cache_set(key, "some result")
        # Advance time past the TTL so the entry appears expired
        future = time.time() + application._CACHE_TTL + 1
        with patch("time.time", return_value=future):
            result = application._cache_get(key)
        self.assertIsNone(result)

    def test_gen_cache_entry_expires(self):
        """A generate cache entry older than _GENERATE_CACHE_TTL must not be returned."""
        key = "ttl_test_gen_key"
        application._gen_cache_set(key, ["http://example.com/x.png"], "pollinations")
        future = time.time() + application._GENERATE_CACHE_TTL + 1
        with patch("time.time", return_value=future):
            urls, provider = application._gen_cache_get(key)
        self.assertIsNone(urls)
        self.assertIsNone(provider)
        # clean up
        with application._GENERATE_CACHE_LOCK:
            application._GENERATE_CACHE.pop(key, None)

    def test_llm_cache_fresh_entry_returned(self):
        """A freshly written LLM cache entry must be returned before TTL expires."""
        key = "ttl_test_llm_fresh"
        application._cache_set(key, "fresh result")
        result = application._cache_get(key)
        self.assertEqual(result, "fresh result")
        # clean up
        with application._resp_cache_lock:
            application._resp_cache.pop(key, None)

    def test_gen_cache_fresh_entry_returned(self):
        """A freshly written generate cache entry must be returned before TTL expires."""
        key = "ttl_test_gen_fresh"
        application._gen_cache_set(key, ["http://example.com/y.png"], "fal.ai")
        urls, provider = application._gen_cache_get(key)
        self.assertEqual(urls, ["http://example.com/y.png"])
        self.assertEqual(provider, "fal.ai")
        # clean up
        with application._GENERATE_CACHE_LOCK:
            application._GENERATE_CACHE.pop(key, None)


if __name__ == "__main__":
    unittest.main()

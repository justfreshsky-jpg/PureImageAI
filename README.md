# PureImage AI

AI-powered image generation web app. Type a text prompt and generate stunning images using multiple AI providers.

## Features

- **Multiple image providers** with automatic fallback chain:
  1. **fal.ai** — Primary (FLUX Schnell, FLUX Pro, SD3, Recraft v3)
  2. **Hugging Face** — Secondary (FLUX.1-schnell, SDXL)
  3. **Stability AI** — Tertiary (SDXL 1.0)
  4. **Replicate** — Quaternary (FLUX Schnell)
  5. **Pollinations.ai** — Free fallback, always available (no key needed)
- **Style presets**: Photorealistic, Artistic, Anime, Digital Art, Oil Painting, Watercolor, Sketch, Cinematic, Abstract
- **Aspect ratios**: Square (1:1), Landscape (16:9), Portrait (9:16), Wide (3:2), Tall (2:3)
- **Multiple images**: Generate 1, 2, or 4 images at once
- **Prompt enhancement**: Optional AI-powered prompt improvement (requires an LLM key)
- **Negative prompts**: Advanced control over what to exclude
- **Download buttons** on every generated image
- **Rate limiting** and **response caching** built in

## Environment Variables

### Image Providers
| Variable | Description |
|---|---|
| `FAL_KEY` | fal.ai API key (https://fal.ai) |
| `HF_KEY` | Hugging Face API key (https://huggingface.co) |
| `STABILITY_KEY` | Stability AI API key (https://stability.ai) |
| `REPLICATE_KEY` | Replicate API key (https://replicate.com) |

Pollinations.ai requires no key and is always used as the final fallback.

> **Minimum working setup (no keys):** Image generation works out of the box via Pollinations.ai,
> which is the free final fallback and requires no API key.
> Set at least one paid provider key (FAL_KEY, HF_KEY, etc.) for better reliability, speed, and resolution.

### Text LLM Keys (for prompt enhancement, optional)
| Variable | Provider |
|---|---|
| `GROQ_KEY` | Groq (Llama 3.3 70B) — recommended, has a generous free tier |
| `CEREBRAS_KEY` | Cerebras |
| `GEMINI_KEY` | Google Gemini |
| `COHERE_KEY` | Cohere |
| `MISTRAL_KEY` | Mistral |
| `OPENROUTER_KEY` | OpenRouter |
| `HF_KEY` | Hugging Face (also used for image generation) |

> **Note:** The **Enhance Prompt** button is only shown in the UI when at least one LLM key is
> configured. Without a key the button is hidden and the `/enhance_prompt` endpoint returns 503
> with an actionable error message.

### App Config
| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | HTTP port |
| `PUREIMAGE_LOG_PATH` | `/tmp/pureimage_feedback.log.jsonl` | Generation log path |

## Run Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) configure API keys
export GROQ_KEY=your-groq-key
export FAL_KEY=your-fal-key
# ... other keys as needed

# 3. Start the server
python app.py
```

Then open http://localhost:8080.

> **No keys?** The app still works — Pollinations.ai (free, no key) is always the final fallback
> in the image provider chain. Set `GROQ_KEY` (or another LLM key) to also enable **Enhance Prompt**.

## Run Tests

```bash
# Install dependencies (if not already done)
pip install -r requirements.txt

# Run all tests with verbose output
pytest tests/ -v
```

Tests cover: cache helpers (LLM and generate caches, TTL expiry), `/enhance_prompt` (happy path + error cases), `/generate` (happy path + error cases), `/health`, `/proxy_image` (SSRF rejection + allowed URL), rate-limit GET exemption, and `request_id` propagation in error responses.

## Deploy on Render

1. Create a new **Web Service**
2. Set **Build Command**: `pip install -r requirements.txt`
3. Set **Start Command**: `gunicorn app:app`
4. Add environment variables for your API keys
5. Deploy — Pollinations.ai works with no keys, so images generate immediately

## Deploy on Google Cloud Run

The app binds to `0.0.0.0` and respects the `$PORT` environment variable (default `8080`), which is required for Cloud Run.

1. Build and push the Docker image:
   ```bash
   gcloud builds submit --tag gcr.io/PROJECT_ID/pureimage-ai
   ```
2. Deploy the service:
   ```bash
   gcloud run deploy pureimage-ai \
     --image gcr.io/PROJECT_ID/pureimage-ai \
     --platform managed \
     --allow-unauthenticated \
     --set-env-vars "GROQ_KEY=your-key,FAL_KEY=your-key"
   ```
3. Add environment variables for your API keys via the Cloud Run console or `--set-env-vars`.

> **PORT binding:** Cloud Run injects the `$PORT` environment variable. The app reads it automatically — no extra configuration needed.

> **Outbound requests:** The app uses the `requests` library for all provider calls. Proxy env vars (`HTTP_PROXY`, `HTTPS_PROXY`) are not set by default on Cloud Run and should not be configured unless your project requires them.

### Image Proxying

Generated images from external providers are served through the `/proxy_image` endpoint to avoid CORS issues. The allowed upstream hosts are defined in `app.py` in the `allowed_hosts` tuple inside `proxy_image()`. If a new image provider returns URLs from a host not in the list, add it there.

### Debugging on Cloud Run

- Visit `/debug` to check which API keys are configured and retrieve the Cloud Run trace ID.
- Check Cloud Run logs for lines containing `Unhandled route error` — each entry includes a `request_id` that is also returned in the JSON error response to the client, making it easy to correlate user-reported errors with server logs.

## Error Reference

| Endpoint | Status | Cause | Resolution |
|---|---|---|---|
| `/enhance_prompt` | 400 | Empty or missing prompt | Enter a non-empty prompt |
| `/enhance_prompt` | 400 | Prompt exceeds 4000 characters | Shorten the prompt |
| `/enhance_prompt` | 503 | No LLM key configured | Set `GROQ_KEY` or another LLM key |
| `/enhance_prompt` | 502 | All LLM providers failed | Check provider API key validity / quota |
| `/enhance_prompt` | 500 | Unexpected server error | Check logs for `request_id`; retry |
| `/generate` | 400 | Empty or missing prompt | Enter a non-empty prompt |
| `/generate` | 400 | Prompt exceeds 4000 characters | Shorten the prompt |
| `/generate` | 429 | Global or per-endpoint rate limit exceeded | Wait ~1 minute; response includes `request_id` |
| `/generate` | 503 | No image provider keys and Pollinations unreachable | Set `FAL_KEY`, `HF_KEY`, `STABILITY_KEY`, or `REPLICATE_KEY` |
| `/generate` | 502 | All providers failed (keys present) | Check provider API key validity / quota |
| `/generate` | 500 | Unexpected server error | Check logs for `request_id`; retry |
| `/proxy_image` | 400 | Missing or disallowed URL | Only URLs from known providers are proxied |
| `/proxy_image` | 502 | Upstream image fetch failed | Provider may be temporarily unavailable |

> **request_id:** All 4xx rate-limit and 5xx responses include a `request_id` field. Use it to correlate
> client-reported errors with server log entries (search for `request_id=<value>` in Cloud Run logs).


# Model profiles

Profiles are Docker environment files selected by `MODEL_PROFILE` in `.env`.
Each pins the Hugging Face revision and the settings that affect capacity.
Both disable Qwen's thinking mode by default so structured Responses output is
not displaced by hidden reasoning tokens. Clients may explicitly override the
chat-template setting when a study requires reasoning mode.

- `qwen3.5-9b-bf16`: default candidate for a 32 GB GPU and 20–30 concurrent
  streams. Declare it supported only after the deployment benchmark passes.
- `qwen3.5-27b-gptq-int4`: experimental. Its official checkpoint occupies about
  30.2 GB before runtime and KV-cache allocations, so it may not provide enough
  headroom on a 32 GB GPU even with an 8K context limit.

Do not change a profile during an active study. Add a new named profile when
changing a model revision, precision, context limit, or scheduler setting so
past deployments remain reproducible.

# Minimal OpenAI client

Requires Python 3.10 or newer and a running Human AI Platform deployment.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with the deployment URL, API key, and advertised model name, then:

```bash
python client.py
```

Expected output:

```text
ready
```

For a DNS/TLS deployment, use a base URL such as
`https://llm.example.edu/v1`. Keep certificate verification enabled and never
commit `.env`.

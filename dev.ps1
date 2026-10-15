# Run the app. The venv lives outside OneDrive so it doesn't sync hundreds of MB over venue wifi.
#   .\dev.ps1            -> start the server on http://localhost:8000
#   .\dev.ps1 smoke      -> check Stripe + Anthropic keys and which Stripe features this account has
#   .\dev.ps1 tunnel     -> ngrok tunnel for Stripe webhooks
$env:UV_PROJECT_ENVIRONMENT = "$HOME\.venvs\briefcase-hack"
switch ($args[0]) {
  "smoke"  { uv run python -m scripts.smoke }
  "tunnel" { ngrok http 8000 }
  # python -m, not the uvicorn.exe shim: Windows Smart App Control blocks unsigned venv launchers.
  default  { uv run python -m uvicorn app.main:app --reload --port 8000 }
}

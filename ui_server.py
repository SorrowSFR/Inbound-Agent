from backend_api import app


if __name__ == "__main__":
    import os

    import uvicorn

    host = str(os.environ.get("UI_HOST") or os.environ.get("HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port_raw = os.environ.get("UI_PORT") or os.environ.get("PORT") or "8000"
    try:
        port = int(str(port_raw).strip())
    except Exception:
        port = 8000
    uvicorn.run("backend_api:app", host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")

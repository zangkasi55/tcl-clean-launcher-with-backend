"""Compatibility entry point for the loopback-only sandbox; no pytest dependency."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("tvcontrol.api:create_sandbox_app", factory=True, host="127.0.0.1", port=8790)

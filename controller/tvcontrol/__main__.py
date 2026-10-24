import argparse
import os

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCL Home Control")
    parser.add_argument("--sandbox", action="store_true",
                        help="Use only a simulated TV, isolated data and a manual clock")
    args = parser.parse_args()
    factory = "tvcontrol.api:create_sandbox_app" if args.sandbox else "tvcontrol.api:create_app"
    host = "127.0.0.1" if args.sandbox else os.environ.get("TVCONTROL_LISTEN", "127.0.0.1")
    port = int(os.environ.get("TVCONTROL_SANDBOX_PORT", "8790") if args.sandbox else
               os.environ.get("TVCONTROL_PORT", "8787"))
    uvicorn.run(factory, factory=True, host=host, port=port)

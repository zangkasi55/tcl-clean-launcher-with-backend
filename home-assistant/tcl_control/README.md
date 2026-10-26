# TCL Control

Use the private TCL controller **inside Home Assistant**, including outside the
home, without making the controller's passwordless LAN portal public.

Home Assistant authenticates the connection using Supervisor ingress. The app
also checks the Supervisor's network address and an explicit HA user-ID
allowlist. No HA password, long-lived access token, ADB key or photo belongs in
the app configuration.

See [setup and security details](./DOCS.md). The controller remains a separate,
private service; this app does not install or wake the TV.

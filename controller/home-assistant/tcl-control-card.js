class TclControlCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-width:0; touch-action:auto; }
        ha-card { overflow:visible; }
        header { padding:16px; }
        h2 { font-size:20px; font-weight:500; margin:0 0 10px; }
        p { line-height:1.5; margin:10px 0 0; }
        a { color:var(--primary-color); }
        button { font:inherit; margin-top:12px; padding:10px 16px; cursor:pointer; }
        iframe { display:block; width:100%; border:0; touch-action:auto;
          height:clamp(360px,calc(100vh - 210px),var(--tcl-height,720px));
          height:clamp(360px,calc(100dvh - 210px),var(--tcl-height,720px)); }
        [hidden] { display:none !important; }
        .error { color:var(--error-color,#c62828); }
      </style>
      <ha-card>
        <header>
          <h2></h2>
          <a>Open full-screen TCL controls</a>
          <p role="status">Connecting through Home Assistant...</p>
          <button type="button" hidden>Retry connection</button>
        </header>
        <iframe title="TCL TV controls" hidden
          sandbox="allow-forms allow-popups allow-same-origin allow-scripts allow-modals allow-downloads"
          allow="fullscreen"></iframe>
      </ha-card>`;
    this._frame = this.shadowRoot.querySelector("iframe");
    this._status = this.shadowRoot.querySelector("[role=status]");
    this._retry = this.shadowRoot.querySelector("button");
    this._retry.addEventListener("click", () => { this._started = false; void this._open(); });
  }

  setConfig(config) {
    if (!config || !/^[a-z0-9_]+$/.test(config.addon || "")) {
      throw new Error("Set addon to the installed TCL Control app slug.");
    }
    const height = config.height ?? 720;
    if (!Number.isInteger(height) || height < 360 || height > 1600) {
      throw new Error("TCL control height must be an integer from 360 to 1600.");
    }
    if (this._config?.addon !== config.addon) {
      this._reset();
      this._started = false;
    }
    this._config = {...config, height};
    this.shadowRoot.querySelector("h2").textContent = config.title || "TCL TV Control";
    this.shadowRoot.querySelector("a").href = `/app/${config.addon}`;
    this.style.setProperty("--tcl-height", `${height}px`);
    void this._open();
  }

  set hass(hass) {
    if (this._hass?.user?.id && this._hass.user.id !== hass?.user?.id) {
      this._reset();
      this._started = false;
    }
    this._hass = hass;
    void this._open();
  }

  getCardSize() { return 12; }
  getGridOptions() { return {columns: 12, rows: 12, min_columns: 6, min_rows: 6}; }

  connectedCallback() { void this._open(); }

  disconnectedCallback() {
    this._reset();
    this._started = false;
  }

  _reset() {
    clearInterval(this._keepAlive);
    this._keepAlive = null;
    this._generation = (this._generation || 0) + 1;
    this._frame.hidden = true;
    this._frame.removeAttribute("src");
  }

  async _session() {
    const result = await this._hass.callWS({
      type: "supervisor/api", endpoint: "/ingress/session", method: "post",
    });
    if (typeof result.session !== "string" || !/^[A-Za-z0-9_-]+$/.test(result.session)) {
      throw new Error("Home Assistant did not create an ingress session.");
    }
    document.cookie = `ingress_session=${result.session};path=/api/hassio_ingress/;SameSite=Strict;Secure`;
    return result.session;
  }

  _error(error) {
    this._status.textContent = `TCL controls unavailable: ${error.message || String(error)}`;
    this._status.classList.add("error");
    this._retry.hidden = false;
  }

  async _open() {
    if (!this.isConnected || !this._config || !this._hass?.connected || this._started) return;
    this._started = true;
    this._reset();
    const generation = this._generation;
    this._status.classList.remove("error");
    this._status.textContent = "Connecting through Home Assistant...";
    this._retry.hidden = true;
    try {
      if (location.protocol !== "https:") {
        throw new Error("Open Home Assistant at its trusted HTTPS address for inline controls.");
      }
      const addon = await this._hass.callWS({
        type: "supervisor/api", endpoint: `/addons/${this._config.addon}/info`, method: "get",
      });
      if (addon.state !== "started" || !addon.ingress_url) {
        throw new Error("Start the TCL Control app in Home Assistant Settings > Apps.");
      }
      const url = new URL(addon.ingress_url, location.origin);
      if (url.origin !== location.origin
          || !/^\/api\/hassio_ingress\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)
          || url.search || url.hash) {
        throw new Error("The TCL app returned an invalid same-origin ingress address.");
      }
      url.pathname = `${url.pathname.replace(/\/$/, "")}/`;
      let session = await this._session();
      if (!this.isConnected || generation !== this._generation) return;
      const check = await fetch(new URL("healthz", url), {
        credentials: "same-origin", cache: "no-store",
      });
      if (!check.ok) {
        throw new Error(`The private controller connection returned HTTP ${check.status}. `
          + "Check the TCL app configuration and logs.");
      }
      const health = await check.json();
      if (health.service !== "ok") throw new Error("The private controller is not healthy.");
      if (!this.isConnected || generation !== this._generation) return;
      this._frame.src = url.href;
      this._frame.hidden = false;
      this._status.textContent = "Scroll inside the controls, or open full screen for more space.";
      this._keepAlive = setInterval(async () => {
        try {
          await this._hass.callWS({
            type: "supervisor/api", endpoint: "/ingress/validate_session",
            method: "post", data: {session},
          });
        } catch (error) {
          try { session = await this._session(); }
          catch (renewalError) {
            clearInterval(this._keepAlive);
            this._error(renewalError);
          }
        }
      }, 60000);
    } catch (error) {
      if (this.isConnected && generation === this._generation) this._error(error);
    }
  }
}

customElements.define("tcl-control-card", TclControlCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "tcl-control-card", name: "TCL TV Control",
  description: "Scrollable TCL controls through authenticated Home Assistant ingress.",
});

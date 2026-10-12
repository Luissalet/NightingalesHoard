// Hooks the shared Faustus copilot launcher into this app: it merges context() into every turn and calls
// onTurnEnd() when an agent turn finishes. Domain knowledge only; the panel itself is injected by the server.
import { api } from "./api.js";
import { CONTEXT_REQUEST, REFRESH_EVENT, buildContext } from "./copilot-core.js";

const ask = (detail) => window.dispatchEvent(new CustomEvent(CONTEXT_REQUEST, { detail }));

window.hoardCopilot = {
  async context() {
    try {
      return await buildContext({ hash: window.location.hash, api, ask });
    } catch {
      return {};
    }
  },
  // The agent may have changed datasets, charts, dashboards, rules or models: let the mounted views reload in place.
  onTurnEnd() {
    window.dispatchEvent(new Event(REFRESH_EVENT));
  },
};

# Assistant API

The shared family assistant uses the existing application server:

- GET /api/copilot/info: origin and stable data-space identity.
- GET /api/copilot/panel.js: the shared launcher and panel.
- /api/copilot/proxy: the common relay to the globally configured Faustus service, including streaming requests.

Credentials remain on the server. Conversation history and agent execution belong to Faustus. The frontend context provider includes the current view, locale, native object IDs and complete unsaved editor fields. New or missing objects have no active ID. Turn completion refreshes native lists and clean editor fields while retaining unsaved text. Delayed dashboard loads check the current selection before applying a response or error.

The existing 18 native MCP tools and their HTTP domain routes are retained; see MCP.md for their contracts. The assistant adds no duplicate local agent loop or conversation store.

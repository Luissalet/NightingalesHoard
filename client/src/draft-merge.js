// Pure decision logic for "a request that was in flight finished while the user kept typing" (no DOM, no React).

/**
 * An editor text was requested (a reload after an agent turn, or the load that follows creating a record) and the
 * answer `loaded` has just arrived.
 *  - started: the saved baseline the editor had when the request started
 *  - current: what the editor holds NOW (read from a ref, not from the closure that started the request)
 * The editor is replaced only when the user has not touched it since the baseline; otherwise the user's text stays.
 * Returns `{ replace, text, baseline }`: the text to show and the new saved baseline. When the user's text is kept the
 * baseline is the loaded text, unless `keepBaselineOnEdit` is set (the create flow: the baseline stays what was sent).
 */
export function settleLoad({ started, current, loaded, keepBaselineOnEdit = false }) {
  if (current === started) return { replace: true, text: loaded, baseline: loaded };
  return { replace: false, text: current, baseline: keepBaselineOnEdit ? started : loaded };
}

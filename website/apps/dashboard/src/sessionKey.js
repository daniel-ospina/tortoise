// #1708 D8: session-key classification from server-provided API data,
// extracted pure so it's unit-testable with node --test (no harness).
// (k: key row, activeKey: the current session's plaintext key, or null)
export function isSessionKey(k, activeKey) {
  if (!k || k.revoked_at) return false
  if (k.created_via === 'bootstrap' || !!k.expires_at) return true
  if (k.created_via == null) {
    // API fields absent (stale cached responses / registry lane pre-#1709):
    // keep the old active-key guard so the live session key can't be revoked
    return !!activeKey && (k.key_prefix === String(activeKey).slice(0, 10))
  }
  return false // durable (created_via 'provisioned'/'recovery'/etc.)
}
// separate guard — the live data-plane key must NEVER be revocable from the
// UI, even when it is a durable key (created_via 'provisioned'):
export function isActiveKey(k, activeKey) {
  return !!activeKey && !k.revoked_at && (k.key_prefix === String(activeKey).slice(0, 10))
}

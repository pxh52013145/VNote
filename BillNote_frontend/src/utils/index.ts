// 解析URL
export function parseUrl(url: string): { protocol: string, host: string, path: string } {
  const urlObj = new URL(url);
  return {
    protocol: urlObj.protocol,
    host: urlObj.host,
    path: urlObj.pathname
  };
}

export async function openExternalUrl(url: string): Promise<void> {
  const target = String(url || '').trim()
  if (!target) return

  try {
    const { open } = await import('@tauri-apps/plugin-shell')
    await open(target)
    return
  } catch (_) {}

  if (typeof window !== 'undefined') {
    window.open(target, '_blank', 'noopener,noreferrer')
  }
}

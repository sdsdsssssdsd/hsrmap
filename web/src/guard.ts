const LOCAL = new Set(["127.0.0.1", "localhost", ""]);

export const networkLog: { blocked: string[]; seen: string[] } = { blocked: [], seen: [] };

export function installNetworkGuard(): void {
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const parsed = new URL(url, window.location.href);
    networkLog.seen.push(parsed.href);
    if (parsed.origin !== window.location.origin && !LOCAL.has(parsed.hostname)) {
      networkLog.blocked.push(parsed.href);
      throw new Error(`blocked remote fetch: ${parsed.href}`);
    }
    return original(input, init);
  };
  (window as unknown as { __HSRMAP_NET: typeof networkLog }).__HSRMAP_NET = networkLog;
}

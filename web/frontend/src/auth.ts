// Attach the optional API token (LLMCI_API_TOKEN on the server) to every
// same-origin /api and /v1 request, including EventSource streams, which
// cannot send headers and use ?token= instead. The token lives in
// localStorage; on a 401 the user is asked for it once and the page reloads.

const KEY = "llmci_token";

function readToken(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

function isApi(url: string): boolean {
  try {
    const u = new URL(url, window.location.href);
    return u.origin === window.location.origin && /^\/(api|v1)\//.test(u.pathname);
  } catch {
    return false;
  }
}

let prompted = false;

export function installAuth(): void {
  const origFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const token = readToken();
    if (token && isApi(url)) {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      headers.set("Authorization", `Bearer ${token}`);
      init = { ...init, headers };
    }
    const res = await origFetch(input, init);
    if (res.status === 401 && isApi(url) && !prompted) {
      prompted = true;
      const next = window.prompt("This API needs a token (LLMCI_API_TOKEN). Paste it to continue:");
      if (next) {
        try {
          localStorage.setItem(KEY, next.trim());
        } catch {
          /* storage blocked: token is not kept */
        }
        window.location.reload();
      }
    }
    return res;
  };

  const OrigES = window.EventSource;
  class AuthedEventSource extends OrigES {
    constructor(url: string | URL, init?: EventSourceInit) {
      const token = readToken();
      let target = String(url);
      if (token && isApi(target)) {
        const u = new URL(target, window.location.href);
        u.searchParams.set("token", token);
        target = u.pathname + u.search;
      }
      super(target, init);
    }
  }
  window.EventSource = AuthedEventSource as typeof EventSource;
}

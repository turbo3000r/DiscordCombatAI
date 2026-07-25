import {
  type AuthenticationResult,
  type IPublicClientApplication,
  PublicClientApplication,
} from "@azure/msal-browser";
import { getRuntime } from "./runtime";

let msalInstance: IPublicClientApplication | null = null;
let initPromise: Promise<IPublicClientApplication | null> | null = null;

function cacheToken(result: AuthenticationResult | null): void {
  if (!result?.accessToken) {
    sessionStorage.removeItem("dca_access_token");
    return;
  }
  sessionStorage.setItem("dca_access_token", result.accessToken);
}

async function ensureMsal(): Promise<IPublicClientApplication | null> {
  const runtime = getRuntime();
  if (runtime.mode !== "production" || !runtime.entra) {
    return null;
  }
  if (msalInstance) {
    return msalInstance;
  }
  if (!initPromise) {
    initPromise = (async () => {
      const instance = new PublicClientApplication({
        auth: {
          clientId: runtime.entra!.clientId,
          authority: runtime.entra!.authority,
          redirectUri: window.location.origin,
        },
        cache: {
          cacheLocation: "sessionStorage",
          storeAuthStateInCookie: false,
        },
      });
      await instance.initialize();
      const redirect = await instance.handleRedirectPromise();
      cacheToken(redirect);
      msalInstance = instance;
      return instance;
    })();
  }
  return initPromise;
}

export async function acquireAccessToken(): Promise<string | null> {
  const runtime = getRuntime();
  if (runtime.mode !== "production") {
    return null;
  }
  const instance = await ensureMsal();
  if (!instance || !runtime.entra) {
    return sessionStorage.getItem("dca_access_token");
  }
  const accounts = instance.getAllAccounts();
  const scopes = [`${runtime.entra.audience}/access_as_admin`];
  if (!accounts.length) {
    await instance.loginRedirect({ scopes });
    return null;
  }
  try {
    const result = await instance.acquireTokenSilent({
      account: accounts[0],
      scopes,
    });
    cacheToken(result);
    return result.accessToken;
  } catch {
    await instance.loginRedirect({ scopes, account: accounts[0] });
    return null;
  }
}

export async function startLogin(): Promise<void> {
  const runtime = getRuntime();
  if (runtime.mode !== "production" || !runtime.entra) {
    return;
  }
  const instance = await ensureMsal();
  if (!instance) {
    return;
  }
  const scopes = [`${runtime.entra.audience}/access_as_admin`];
  await instance.loginRedirect({ scopes });
}

export type RuntimeConfig = {
  mode: "production" | "development";
  localAdminOid: string | null;
  entra: {
    tenantId: string;
    clientId: string;
    audience: string;
    authority: string;
  } | null;
};

declare global {
  interface Window {
    __DCA_RUNTIME__?: RuntimeConfig;
  }
}

export function getRuntime(): RuntimeConfig {
  return (
    window.__DCA_RUNTIME__ || {
      mode: "development",
      localAdminOid: "local-dev-admin",
      entra: null,
    }
  );
}

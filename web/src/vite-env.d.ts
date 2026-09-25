/// <reference types="vite/client" />

/** Injected by `vite.config.ts` (`define`). Literal `true`/`false` so the whole
 *  mock branch — including the MSW dynamic import — is dropped from production
 *  builds (CONTRACT §11 #11). */
declare const __ENABLE_MOCKS__: boolean;

interface ImportMetaEnv {
  readonly VITE_ENABLE_MOCKS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

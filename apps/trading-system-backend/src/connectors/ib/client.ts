import { ConnectionState, IBApiNext } from '@stoqey/ib';

import { env } from '../../env.js';

// The dockerized IB Gateway (see infra/docker-compose.yml) restarts on its own schedule
// (AUTO_RESTART_TIME) and drops the API socket while it does -- IBApiNext's built-in
// `reconnectInterval` handles reattaching automatically rather than us needing a manual
// reconnect loop. One process-wide connection, shared by every IB-sourced instrument.
let client: IBApiNext | null = null;

export const getIbClient = (): IBApiNext => {
  if (!client) {
    client = new IBApiNext({
      host: env.IB_GATEWAY_HOST,
      port: env.IB_GATEWAY_PORT,
      reconnectInterval: 5000
    });

    client.connectionState.subscribe(state => {
      console.info(`[ib] connection state: ${ConnectionState[state]}`);
    });
    client.error.subscribe(({ error, code, reqId }) => {
      // reqId -1 is a connection-level notice (e.g. "Market data farm connection is OK"), not a
      // real error -- IB sends a lot of these as informational noise.
      if (reqId === -1) return;
      // Code 200 ("No security definition has been found for the request") is IB's normal answer
      // for a symbol that doesn't (yet) resolve to a real contract -- searchSymbols's CONTFUT
      // probe hits this constantly while a user is still mid-word in the search box (see
      // ibConnector.ts's RETRYABLE_IB_ERROR_CODES, which already treats this as non-transient and
      // doesn't retry it). Logging it as a warning on every keystroke is misleading noise, not a
      // real problem -- nothing here fails because of it.
      if ((code as number) === 200) return;
      console.warn(`[ib] error ${code} on request ${reqId}: ${error.message}`);
    });

    client.connect(env.IB_CLIENT_ID);
  }

  return client;
};

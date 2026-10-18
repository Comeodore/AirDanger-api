export class MemoryStorage {}

export class WebCryptoProvider {}

export class WebSocketTransport {}

export class TelegramClient {
  constructor() {
    this.onNewMessage = { add: () => {} };
  }

  async start() {
    return { username: "stub" };
  }

  async close() {}
}

export function convertFromTelethonSession(session) {
  return session;
}

export default new Uint8Array();

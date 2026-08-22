export class LocalDataPlaneClient {
  ensurePaired(opts: {
    ownerType?: string;
    ownerId: string;
    deviceId?: string;
    agentId?: string;
    scopes?: string[];
  }): Promise<{ info: { device_id?: string }; agent: unknown; session: unknown }>;
  uploadAssets(...args: unknown[]): Promise<unknown>;
  listAssets(...args: unknown[]): Promise<unknown>;
  listOutputs(...args: unknown[]): Promise<unknown>;
  clearSessions(): void;
}

export const localDataPlane: LocalDataPlaneClient;
export function clearLocalPairingSessions(): void;

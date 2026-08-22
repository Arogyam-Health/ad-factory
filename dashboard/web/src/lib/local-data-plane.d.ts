export class LocalDataPlaneClient {
  ensurePaired(opts: {
    ownerType?: string;
    ownerId: string;
    deviceId?: string;
    agentId?: string;
    scopes?: string[];
  }): Promise<{ info: { device_id?: string }; agent: unknown; session: unknown }>;
  uploadAssets(
    files: FileList | File[],
    opts?: { kind?: string; deviceId?: string; operationId?: string },
  ): Promise<{ resource_id?: string; version?: number; filename?: string }[]>;
  listAssets(opts?: { kind?: string; deviceId?: string }): Promise<{ resource_id: string; version?: number; filename?: string; kind?: string }[]>;
  assetObjectUrl(resourceId: string, deviceId?: string, version?: number): Promise<string>;
  listOutputs(...args: unknown[]): Promise<unknown>;
  clearSessions(): void;
}

export const localDataPlane: LocalDataPlaneClient;
export function clearLocalPairingSessions(): void;

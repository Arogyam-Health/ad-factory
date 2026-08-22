export class LocalDataPlaneClient {
  ensurePaired(opts: {
    ownerType?: string;
    ownerId: string;
    deviceId?: string;
    agentId?: string;
    scopes?: string[];
  }): Promise<{ info: { device_id?: string }; agent: unknown; session: unknown }>;
  allocateLocalRun(opts: {
    ownerType?: string;
    ownerId: string;
    flowType: string;
    settings?: Record<string, unknown>;
  }): Promise<{ run_id: string; device_id?: string; display_batch?: string; run_number?: number }>;
  uploadAssets(
    files: FileList | File[],
    opts?: { kind?: string; deviceId?: string; operationId?: string },
  ): Promise<{ resource_id?: string; version?: number; filename?: string }[]>;
  listAssets(opts?: { kind?: string; deviceId?: string }): Promise<{ resource_id: string; version?: number; filename?: string; kind?: string }[]>;
  deleteAsset(resourceId: string, opts?: { deviceId?: string; operationId?: string }): Promise<unknown>;
  assetObjectUrl(resourceId: string, deviceId?: string, version?: number): Promise<string>;
  putText(
    collection: "documents" | "configs",
    logicalKey: string,
    content: string,
    opts?: { deviceId?: string; operationId?: string; runId?: string; role?: string; expectedVersion?: number },
  ): Promise<{ resource_id: string; version?: number }>;
  listOutputs(...args: unknown[]): Promise<unknown>;
  deleteRun?(runId: string, deviceId?: string): Promise<unknown>;
  clearSessions(): void;
}

export const localDataPlane: LocalDataPlaneClient;
export function clearLocalPairingSessions(): void;

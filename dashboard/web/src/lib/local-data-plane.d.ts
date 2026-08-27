export class LocalDataPlaneClient {
  discover(): Promise<{ device_id?: string }>;
  restoreStoredSession(preferredOwners?: { ownerType?: string; owner_type?: string; ownerId?: string; owner_id?: string }[]):
    | { deviceId: string; agentId: string; session: unknown }
    | null;
  session(deviceId?: string, ownerKey?: string): {
    owner_type?: string;
    owner_id?: string;
    access_token?: string;
    agent_id?: string;
    scopes?: string[];
  } | null;
  ensurePaired(opts: {
    ownerType?: string;
    ownerId: string;
    deviceId?: string;
    agentId?: string;
    scopes?: string[];
  }): Promise<{ info: { device_id?: string }; agent: { agent_id?: string }; session: unknown }>;
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
  listOutputs(runId: string, deviceId?: string): Promise<{ output_id?: string; resource_id?: string; version?: number; current_version?: number; filename?: string; display_name?: string; aspect_ratio?: string }[]>;
  listPrompts(runId: string, deviceId?: string): Promise<{ prompt_id?: string; version?: number; resource_version?: number; format?: string; persona?: string; persona_name?: string; display_name?: string; language?: string; status?: string }[]>;
  promptContent(promptId: string, deviceId?: string, version?: number): Promise<string>;
  putPrompt(promptId: string, runId: string, content: string, expectedVersion: number, deviceId?: string): Promise<{ version?: number }>;
  outputObjectUrl(outputId: string, deviceId?: string, version?: number): Promise<string>;
  outputRawBlob(outputId: string, deviceId?: string): Promise<Blob>;
  outputAction(outputId: string, action: string, deviceId?: string, payload?: Record<string, unknown>): Promise<{ revision_id?: string; status?: string }>;
  revisionStatus(revisionId: string, deviceId?: string): Promise<{ status?: string; error?: string }>;
  deleteOutput(outputId: string, deviceId?: string): Promise<unknown>;
  downloadRun(runId: string, deviceId?: string, opts?: { includeRaw?: boolean }): Promise<Blob>;
  listRuns(deviceId?: string): Promise<Array<{
    run_id?: string;
    display_batch?: string;
    flow_type?: string;
    created_at?: number;
    status?: string;
    prompt_count?: number;
    image_count?: number;
  }>>;
  deleteRun?(runId: string, deviceId?: string): Promise<unknown>;
  clearSessions(): void;
}

export const localDataPlane: LocalDataPlaneClient;
export function clearLocalPairingSessions(): void;

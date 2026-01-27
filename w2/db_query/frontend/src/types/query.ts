/** Query execution types. */

export interface QueryColumn {
  name: string;
  dataType: string;
}

export interface QueryResult {
  columns: QueryColumn[];
  rows: Record<string, any>[];
  rowCount: number;
  executionTimeMs: number;
  sql: string;
}

export interface QueryInput {
  sql: string;
}

export interface QueryHistoryEntry {
  id: number;
  databaseName: string;
  sqlText: string;
  executedAt: string;
  executionTimeMs?: number | null;
  rowCount?: number | null;
  success: boolean;
  errorMessage?: string | null;
  querySource: "manual" | "natural_language";
}

/** AI intent recognition result. */
export interface QueryIntent {
  execute: boolean;
  export: boolean;
  exportFormat: "csv" | "json" | null;
}

/** Natural language query response with AI intent. */
export interface NaturalQueryResponse {
  sql: string;
  explanation: string;
  intent: QueryIntent;
}

import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

/**
 * Read-only bridge to the Python agent's SQLite database for Call Analytics.
 *
 * Server-only — never import this from a client component.
 */

export interface CallLog {
  call_id: number;
  session_id: string;
  call_type: string;
  caller_identifier: string;
  outcome: 'success' | 'failed' | string;
  outcome_reason: string;
  duration_sec: number;
  created_at: string;
}

export interface AnalyticsQueryResult {
  total_calls: number;
  successful_calls: number;
  failed_calls: number;
  success_rate: string;
  recent_calls: CallLog[];
  dbPath: string;
  error?: string;
  tableMissing?: boolean;
}

function resolveDbPath(): string {
  const configured = process.env.ANALYTICS_DB_PATH || process.env.ESCALATION_DB_PATH;
  if (configured && configured.trim()) {
    return path.resolve(process.cwd(), configured.trim());
  }
  return path.join(process.cwd(), '..', 'backend', 'health_memory.db');
}

export function getCallAnalytics(limit = 50): AnalyticsQueryResult {
  const dbPath = resolveDbPath();
  let db: DatabaseSync | undefined;

  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
    
    const totalRow = db.prepare('SELECT COUNT(*) as count FROM call_analytics').get() as { count: number } | undefined;
    const total_calls = Number(totalRow?.count ?? 0);

    const successRow = db.prepare("SELECT COUNT(*) as count FROM call_analytics WHERE outcome = 'success'").get() as { count: number } | undefined;
    const successful_calls = Number(successRow?.count ?? 0);

    const failedRow = db.prepare("SELECT COUNT(*) as count FROM call_analytics WHERE outcome = 'failed'").get() as { count: number } | undefined;
    const failed_calls = Number(failedRow?.count ?? 0);

    const recent_calls = db
      .prepare(
        `SELECT call_id, session_id, call_type, caller_identifier, outcome,
                outcome_reason, duration_sec, created_at
         FROM call_analytics
         ORDER BY call_id DESC
         LIMIT ?`
      )
      .all(limit) as unknown as CallLog[];

    const rawRate = total_calls > 0 ? (successful_calls / total_calls) * 100 : 0;
    const success_rate = `${rawRate.toFixed(1)}%`;

    return {
      total_calls,
      successful_calls,
      failed_calls,
      success_rate,
      recent_calls,
      dbPath,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/no such table/i.test(message)) {
      return {
        total_calls: 0,
        successful_calls: 0,
        failed_calls: 0,
        success_rate: '0.0%',
        recent_calls: [],
        dbPath,
        tableMissing: true,
      };
    }
    return {
      total_calls: 0,
      successful_calls: 0,
      failed_calls: 0,
      success_rate: '0.0%',
      recent_calls: [],
      dbPath,
      error: message,
    };
  } finally {
    db?.close();
  }
}

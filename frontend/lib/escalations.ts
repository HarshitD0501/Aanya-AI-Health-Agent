import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

/**
 * Read-only bridge to the Python agent's SQLite database.
 *
 * Day 7 escalations are written by `backend/src/escalations.py`; this file only
 * ever reads them. The connection is opened with `readOnly: true` so the
 * dashboard can never lock or corrupt the writer that the voice agent depends on.
 *
 * Server-only — never import this from a client component.
 */

export interface Escalation {
  escalation_id: number;
  reference_id: string;
  caller_name: string;
  phone_number: string;
  language: string;
  reason_code: string;
  urgency: string;
  what_happened: string;
  already_checked: string;
  followup_method: string;
  status: string;
  delivery_status: string;
  delivery_detail: string;
  created_at: string;
}

export interface EscalationQueryResult {
  rows: Escalation[];
  dbPath: string;
  /** Set when the database could not be read at all. */
  error?: string;
  /** True when the DB exists but the agent has not created the table yet. */
  tableMissing?: boolean;
}

export const REASON_LABELS: Record<string, string> = {
  red_flag_symptom: 'Red-flag symptom',
  diagnosis_request: 'Diagnosis / prescription / report request',
};

function resolveDbPath(): string {
  const configured = process.env.ESCALATION_DB_PATH;
  if (configured && configured.trim()) {
    return path.resolve(process.cwd(), configured.trim());
  }
  return path.join(process.cwd(), '..', 'backend', 'health_memory.db');
}

export function getEscalations(limit = 100): EscalationQueryResult {
  const dbPath = resolveDbPath();
  let db: DatabaseSync | undefined;

  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
    const rows = db
      .prepare(
        `SELECT escalation_id, reference_id, caller_name, phone_number, language,
                reason_code, urgency, what_happened, already_checked,
                followup_method, status, delivery_status, delivery_detail, created_at
         FROM escalations
         ORDER BY escalation_id DESC
         LIMIT ?`
      )
      .all(limit) as unknown as Escalation[];

    return { rows, dbPath };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // A fresh checkout has no escalations table until the agent runs once. That
    // is an empty queue, not a broken dashboard.
    if (/no such table/i.test(message)) {
      return { rows: [], dbPath, tableMissing: true };
    }
    return { rows: [], dbPath, error: message };
  } finally {
    db?.close();
  }
}

export type Session = {user_id: string; email: string; email_verified: boolean; csrf_token: string; telegram_linked: boolean};
export type Connection = {linked: boolean; username: string | null; display_name: string | null};
export type LinkRequest = {id: string; status: "pending_telegram" | "pending_web_confirmation" | "completed" | "expired" | "cancelled"; expires_at: string; deep_link?: string | null; candidate?: {username: string | null; display_name: string | null} | null};
export type Load = {value: string; unit: "kg" | "lb"; kilograms: string};
export type PerformedSet = {id: string; set_number: number; repetitions: number; load: Load | null; notes: string | null};
export type Exercise = {id: string; name: string; normalized_name: string};
export type CreateExerciseResult = {exercise: Exercise; created: boolean};
export type WorkoutExercise = {id: string; exercise: Exercise; position: number; notes: string | null; sets: PerformedSet[]};
export type ProgramWorkout = {id: string; day_number: number; alias: string; notes: string | null; active: boolean};
export type Workout = {id: string; user_id: string; performed_on: string; status: "draft" | "completed"; notes: string | null; created_at: string; completed_at: string | null; program_workout: ProgramWorkout | null; exercises: WorkoutExercise[]};
export type WorkoutPage = {items: Workout[]; next_cursor: string | null};
export type ExerciseCatalog = {items: Exercise[]};
export type ExerciseHistoryItem = {workout_id: string; performed_on: string; workout_notes: string | null; occurrences: WorkoutExercise[]};
export type ExerciseHistoryPage = {exercise: Exercise; items: ExerciseHistoryItem[]; next_cursor: string | null};
export type StatisticsPeriod = "4w" | "12w" | "1y" | "all";
export type StatisticsWindow = {period: StatisticsPeriod; from_date: string | null; to_date: string};
export type MassMetric = {value: string; unit: "kg" | "lb"; kilograms: string};
export type VolumeMetric = {value: string; load_unit: "kg" | "lb"; kilogram_repetitions: string};
export type ExerciseStatisticsPoint = {
  workout_id: string; performed_on: string; set_count: number; repetition_count: number;
  max_set_repetitions: number; top_load: MassMetric | null;
  estimated_one_rep_max: MassMetric | null; external_volume: VolumeMetric | null;
};
export type ExerciseStatistics = {
  exercise: Exercise; period: StatisticsWindow;
  summary: {
    session_count: number; set_count: number; repetition_count: number; max_set_repetitions: number;
    best_load: MassMetric | null; best_estimated_one_rep_max: MassMetric | null;
    best_session_volume: VolumeMetric | null;
  };
  series: ExerciseStatisticsPoint[];
};
export type OverviewTotals = {
  workout_count: number; active_day_count: number; set_count: number;
  repetition_count: number; external_volume: VolumeMetric | null;
};
export type OverviewStatistics = {
  period: StatisticsWindow; bucket: "week" | "month"; current: OverviewTotals;
  previous: OverviewTotals | null;
  series: Array<{period_start: string; workout_count: number; set_count: number; repetition_count: number; external_volume: VolumeMetric | null}>;
  top_exercises: Array<{exercise_id: string; exercise_name: string; workout_count: number; set_count: number}>;
  recent_records: Array<{exercise_id: string; exercise_name: string; workout_id: string; performed_on: string; estimated_one_rep_max: MassMetric; previous_best: MassMetric}>;
};

let csrf: string | null = null;
export class ApiError extends Error {constructor(public status: number, public code: string, message: string) {super(message);}}

export function setCsrf(value: string | null) {csrf = value;}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, {...options, headers, credentials: "include"});
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {detail?: {code?: string; message?: string}} | null;
    throw new ApiError(response.status, body?.detail?.code ?? "request_failed", body?.detail?.message ?? "Something went wrong");
  }
  if (response.status === 204 || response.headers.get("content-length") === "0") return undefined as T;
  return response.json() as Promise<T>;
}

function withCursor(path: string, limit: number, cursor?: string): string {
  const params = new URLSearchParams({limit: String(limit)});
  if (cursor) params.set("cursor", cursor);
  return `${path}?${params}`;
}

function withPeriod(path: string, period: StatisticsPeriod): string {
  return `${path}?${new URLSearchParams({period})}`;
}

export const historyApi = {
  workouts: (limit: number, cursor?: string) => api<WorkoutPage>(withCursor("/api/me/workouts", limit, cursor)),
  exercises: () => api<ExerciseCatalog>("/api/me/exercises"),
  createExercise: (name: string) => api<CreateExerciseResult>("/api/me/exercises", {method: "POST", body: JSON.stringify({name})}),
  renameExercise: (exerciseId: string, name: string) => api<Exercise>(`/api/me/exercises/${exerciseId}`, {method: "PATCH", body: JSON.stringify({name})}),
  exerciseHistory: (exerciseId: string, limit: number, cursor?: string) => api<ExerciseHistoryPage>(withCursor(`/api/me/exercises/${exerciseId}/history`, limit, cursor)),
  overviewStatistics: (period: StatisticsPeriod) => api<OverviewStatistics>(withPeriod("/api/me/statistics/overview", period)),
  exerciseStatistics: (exerciseId: string, period: StatisticsPeriod) => api<ExerciseStatistics>(withPeriod(`/api/me/exercises/${exerciseId}/statistics`, period)),
};

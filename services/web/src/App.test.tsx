import {afterAll, afterEach, beforeAll, describe, expect, it} from "vitest";
import {cleanup, render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {MemoryRouter} from "react-router-dom";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {http, HttpResponse} from "msw";
import {setupServer} from "msw/node";
import {App} from "./App";
import {setCsrf} from "./api";

const server = setupServer();
beforeAll(() => server.listen({onUnhandledRequest: "error"}));
afterEach(() => {cleanup(); server.resetHandlers(); setCsrf(null)});
afterAll(() => server.close());

function renderAt(path: string) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}, mutations: {retry: false}}});
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>);
}

const session = {user_id: "u1", email: "user@example.com", email_verified: true, csrf_token: "csrf-test", telegram_linked: false};
const workout = {
  id: "w1", user_id: "u1", performed_on: "2026-08-20", status: "completed",
  notes: "Upper body", created_at: "2026-08-20T10:00:00Z", completed_at: "2026-08-20T11:00:00Z", program_workout: null,
  exercises: [{id: "we1", position: 1, notes: null, exercise: {id: "e1", name: "Bench Press", normalized_name: "bench press"}, sets: [{id: "s1", set_number: 1, repetitions: 8, load: {value: "80.000", unit: "kg", kilograms: "80.000"}, notes: null}]}],
};
const overviewStatistics = {
  period: {period: "4w", from_date: "2026-07-27", to_date: "2026-08-23"}, bucket: "week",
  current: {workout_count: 3, active_day_count: 3, set_count: 12, repetition_count: 96, external_volume: {value: "7200.000", load_unit: "kg", kilogram_repetitions: "7200.000"}},
  previous: {workout_count: 2, active_day_count: 2, set_count: 8, repetition_count: 64, external_volume: {value: "5000.000", load_unit: "kg", kilogram_repetitions: "5000.000"}},
  series: [{period_start: "2026-08-17", workout_count: 3, set_count: 12, repetition_count: 96, external_volume: {value: "7200.000", load_unit: "kg", kilogram_repetitions: "7200.000"}}],
  top_exercises: [{exercise_id: "e1", exercise_name: "Bench Press", workout_count: 3, set_count: 12}],
  recent_records: [{exercise_id: "e1", exercise_name: "Bench Press", workout_id: "w1", performed_on: "2026-08-20", estimated_one_rep_max: {value: "101.333", unit: "kg", kilograms: "101.333"}, previous_best: {value: "98.000", unit: "kg", kilograms: "98.000"}}],
};
const exerciseStatistics = {
  exercise: {id: "e1", name: "Bench Press", normalized_name: "bench press"},
  period: {period: "4w", from_date: "2026-07-27", to_date: "2026-08-23"},
  summary: {session_count: 1, set_count: 1, repetition_count: 8, max_set_repetitions: 8, best_load: {value: "80.000", unit: "kg", kilograms: "80.000"}, best_estimated_one_rep_max: {value: "101.333", unit: "kg", kilograms: "101.333"}, best_session_volume: {value: "640.000", load_unit: "kg", kilogram_repetitions: "640.000"}},
  series: [{workout_id: "w1", performed_on: "2026-08-20", set_count: 1, repetition_count: 8, max_set_repetitions: 8, top_load: {value: "80.000", unit: "kg", kilograms: "80.000"}, estimated_one_rep_max: {value: "101.333", unit: "kg", kilograms: "101.333"}, external_volume: {value: "640.000", load_unit: "kg", kilogram_repetitions: "640.000"}}],
};

describe("authentication and optional integrations", () => {
  it("renders registration", () => {
    renderAt("/register");
    expect(screen.getByRole("heading", {name: "Create your account"})).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("redirects an unauthenticated account route to login", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json(
      {detail: {code: "authentication_required", message: "Authentication required"}},
      {status: 401},
    )));
    renderAt("/account");
    expect(await screen.findByRole("heading", {name: "Welcome back"})).toBeInTheDocument();
  });

  it("starts Telegram linking with the session CSRF token", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json({user_id: "u1", email: "user@example.com", email_verified: true, csrf_token: "csrf-test", telegram_linked: false})),
      http.get("/api/me/telegram-connection", () => HttpResponse.json({linked: false, username: null, display_name: null})),
      http.post("/api/me/telegram-link-requests", ({request}) => {
        if (request.headers.get("X-CSRF-Token") !== "csrf-test") return new HttpResponse(null, {status: 403});
        return HttpResponse.json({id: "request-1", status: "pending_telegram", expires_at: "2099-01-01T00:00:00Z", deep_link: "https://t.me/jim007_bot?start=link_token"}, {status: 201});
      }),
      http.get("/api/me/telegram-link-requests/request-1", () => HttpResponse.json({id: "request-1", status: "pending_telegram", expires_at: "2099-01-01T00:00:00Z"})),
    );
    const open = window.open;
    window.open = () => null;
    renderAt("/account");
    await userEvent.click(await screen.findByRole("button", {name: "Connect Telegram"}));
    expect(await screen.findByText("Waiting for you to open Telegram…")).toBeInTheDocument();
    window.open = open;
  });

  it("renders recent activity on the authenticated dashboard", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/workouts", () => HttpResponse.json({items: [workout], next_cursor: null})),
      http.get("/api/me/statistics/overview", () => HttpResponse.json(overviewStatistics)),
    );
    renderAt("/");
    expect(await screen.findByRole("heading", {name: "Dashboard"})).toBeInTheDocument();
    expect((await screen.findAllByText("Bench Press")).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", {name: "View all workouts"})).toHaveAttribute("href", "/workouts");
    expect(screen.getByText("Recent records")).toBeInTheDocument();
    expect(screen.getAllByText("+50%").length).toBeGreaterThan(0);
  });

  it("keeps recent workouts visible when dashboard statistics fail", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/workouts", () => HttpResponse.json({items: [workout], next_cursor: null})),
      http.get("/api/me/statistics/overview", () => HttpResponse.json({detail: {code: "statistics_failed", message: "Statistics unavailable"}}, {status: 500})),
    );
    renderAt("/");
    expect(await screen.findByText("Statistics unavailable")).toBeInTheDocument();
    expect((await screen.findAllByText("Bench Press")).length).toBeGreaterThan(0);
  });

  it("shows exercise progress and refetches it when the period changes", async () => {
    let requestedPeriod = "";
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises/e1/history", () => HttpResponse.json({exercise: exerciseStatistics.exercise, items: [{workout_id: "w1", performed_on: "2026-08-20", workout_notes: null, occurrences: workout.exercises}], next_cursor: null})),
      http.get("/api/me/exercises/e1/statistics", ({request}) => {
        requestedPeriod = new URL(request.url).searchParams.get("period") ?? "";
        return HttpResponse.json(exerciseStatistics);
      }),
    );
    renderAt("/exercises/e1");
    expect(await screen.findByText("Estimated 1RM and top load")).toBeInTheDocument();
    expect(screen.getByText("101.333 kg")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name: "12 weeks"}));
    await waitFor(() => expect(requestedPeriod).toBe("12w"));
  });

  it("renames an exercise from its detail page and updates the title", async () => {
    let releaseRequest!: () => void;
    const requestPending = new Promise<void>(resolve => {releaseRequest = resolve});
    let currentExercise = exerciseStatistics.exercise;
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises/e1/history", () => HttpResponse.json({exercise: currentExercise, items: [], next_cursor: null})),
      http.get("/api/me/exercises/e1/statistics", () => HttpResponse.json({...exerciseStatistics, exercise: currentExercise})),
      http.get("/api/me/exercises", () => HttpResponse.json({items: [currentExercise]})),
      http.patch("/api/me/exercises/e1", async ({request}) => {
        expect(request.headers.get("X-CSRF-Token")).toBe("csrf-test");
        expect(await request.json()).toEqual({name: "Barbell Bench Press"});
        await requestPending;
        currentExercise = {id: "e1", name: "Barbell Bench Press", normalized_name: "barbell bench press"};
        return HttpResponse.json(currentExercise);
      }),
    );
    renderAt("/exercises/e1");
    await userEvent.click(await screen.findByRole("button", {name: "Rename exercise"}));
    const input = screen.getByRole("textbox", {name: "Exercise name"});
    expect(input).toHaveValue("Bench Press");
    await userEvent.click(screen.getByRole("button", {name: "Cancel"}));
    expect(screen.queryByRole("textbox", {name: "Exercise name"})).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", {name: "Rename exercise"}));
    const reopenedInput = screen.getByRole("textbox", {name: "Exercise name"});
    await userEvent.clear(reopenedInput);
    await userEvent.type(reopenedInput, "Barbell Bench Press");
    await userEvent.click(screen.getByRole("button", {name: "Save name"}));
    expect(screen.getByRole("button", {name: "Saving…"})).toBeDisabled();
    releaseRequest();

    expect(await screen.findByRole("heading", {name: "Barbell Bench Press"})).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Exercise renamed successfully.");
    expect(screen.queryByRole("textbox", {name: "Exercise name"})).not.toBeInTheDocument();
  });

  it("keeps the rename form actionable when the new name conflicts", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises/e1/history", () => HttpResponse.json({exercise: exerciseStatistics.exercise, items: [], next_cursor: null})),
      http.get("/api/me/exercises/e1/statistics", () => HttpResponse.json(exerciseStatistics)),
      http.patch("/api/me/exercises/e1", () => HttpResponse.json({detail: {code: "exercise_name_conflict", message: "An exercise with this name already exists"}}, {status: 409})),
    );
    renderAt("/exercises/e1");
    await userEvent.click(await screen.findByRole("button", {name: "Rename exercise"}));
    const input = screen.getByRole("textbox", {name: "Exercise name"});
    await userEvent.clear(input);
    await userEvent.type(input, "Squat");
    await userEvent.click(screen.getByRole("button", {name: "Save name"}));

    expect(await screen.findByRole("alert")).toHaveTextContent("An exercise with this name already exists");
    expect(screen.getByRole("textbox", {name: "Exercise name"})).toHaveValue("Squat");
  });

  it("uses repetition progress for an exercise without loaded sets", async () => {
    const bodyweight = {
      ...exerciseStatistics,
      exercise: {id: "e2", name: "Pull-up", normalized_name: "pull-up"},
      summary: {...exerciseStatistics.summary, best_load: null, best_estimated_one_rep_max: null, best_session_volume: null, max_set_repetitions: 15},
      series: [{...exerciseStatistics.series[0], top_load: null, estimated_one_rep_max: null, external_volume: null, repetition_count: 15, max_set_repetitions: 15}],
    };
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises/e2/history", () => HttpResponse.json({exercise: bodyweight.exercise, items: [], next_cursor: null})),
      http.get("/api/me/exercises/e2/statistics", () => HttpResponse.json(bodyweight)),
    );
    renderAt("/exercises/e2");
    expect(await screen.findByText(/no loaded sets/i)).toBeInTheDocument();
    expect(screen.getByText("Repetitions per session")).toBeInTheDocument();
  });

  it("expands a workout and links to exercise history", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/workouts", () => HttpResponse.json({items: [workout], next_cursor: null})),
    );
    renderAt("/workouts");
    await userEvent.click(await screen.findByRole("button", {name: /View details/}));
    expect(screen.getByText("80 kg")).toBeInTheDocument();
    expect(screen.getByRole("link", {name: "Bench Press"})).toHaveAttribute("href", "/exercises/e1");
  });

  it("filters the exercise directory", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises", () => HttpResponse.json({items: [
        {id: "e1", name: "Bench Press", normalized_name: "bench press"},
        {id: "e2", name: "Lat Pulldown", normalized_name: "lat pulldown"},
      ]})),
    );
    renderAt("/exercises");
    const search = await screen.findByRole("searchbox", {name: "Search exercises"});
    await userEvent.type(search, "lat");
    expect(screen.getByRole("link", {name: /Lat Pulldown/})).toBeInTheDocument();
    expect(screen.queryByRole("link", {name: /Bench Press/})).not.toBeInTheDocument();
  });

  it("creates an exercise with CSRF protection and updates the directory", async () => {
    let releaseRequest!: () => void;
    const requestPending = new Promise<void>(resolve => {releaseRequest = resolve});
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises", () => HttpResponse.json({items: []})),
      http.post("/api/me/exercises", async ({request}) => {
        expect(request.headers.get("X-CSRF-Token")).toBe("csrf-test");
        expect(await request.json()).toEqual({name: "Lat Pulldown"});
        await requestPending;
        return HttpResponse.json({exercise: {id: "e2", name: "Lat Pulldown", normalized_name: "lat pulldown"}, created: true}, {status: 201});
      }),
    );
    renderAt("/exercises");
    await userEvent.click(await screen.findByRole("button", {name: "Add exercise"}));
    await userEvent.type(screen.getByRole("textbox", {name: "Exercise name"}), "Lat Pulldown");
    await userEvent.click(screen.getByRole("button", {name: "Save exercise"}));
    expect(screen.getByRole("button", {name: "Saving…"})).toBeDisabled();
    releaseRequest();
    expect(await screen.findByRole("link", {name: /Lat Pulldown/})).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Exercise added to your library.");
    expect(screen.queryByRole("textbox", {name: "Exercise name"})).not.toBeInTheDocument();
  });

  it("reports an existing exercise without adding a duplicate", async () => {
    const existing = {id: "e1", name: "Bench Press", normalized_name: "bench press"};
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises", () => HttpResponse.json({items: [existing]})),
      http.post("/api/me/exercises", () => HttpResponse.json({exercise: existing, created: false})),
    );
    renderAt("/exercises");
    await userEvent.click(await screen.findByRole("button", {name: "Add exercise"}));
    await userEvent.type(screen.getByRole("textbox", {name: "Exercise name"}), "bench press");
    await userEvent.click(screen.getByRole("button", {name: "Save exercise"}));
    expect(await screen.findByRole("status")).toHaveTextContent("already in your library");
    expect(screen.getAllByRole("link", {name: /Bench Press/})).toHaveLength(1);
  });

  it("can cancel the form and keeps API errors actionable", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/exercises", () => HttpResponse.json({items: []})),
      http.post("/api/me/exercises", () => HttpResponse.json({detail: {code: "invalid_exercise", message: "Exercise could not be added"}}, {status: 422})),
    );
    renderAt("/exercises");
    await userEvent.click(await screen.findByRole("button", {name: "Add exercise"}));
    await userEvent.click(screen.getByRole("button", {name: "Cancel"}));
    expect(screen.queryByRole("textbox", {name: "Exercise name"})).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", {name: "Add exercise"}));
    await userEvent.type(screen.getByRole("textbox", {name: "Exercise name"}), "Squat");
    await userEvent.click(screen.getByRole("button", {name: "Save exercise"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("Exercise could not be added");
    expect(screen.getByRole("textbox", {name: "Exercise name"})).toHaveValue("Squat");
  });

  it("loads the next workout page from the opaque cursor", async () => {
    const second = {...workout, id: "w2", performed_on: "2026-08-19", notes: "Second workout"};
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json(session)),
      http.get("/api/me/workouts", ({request}) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        return HttpResponse.json(cursor ? {items: [second], next_cursor: null} : {items: [workout], next_cursor: "next-page"});
      }),
    );
    renderAt("/workouts");
    await userEvent.click(await screen.findByRole("button", {name: "Load more"}));
    expect(await screen.findByText("Second workout")).toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Load more"})).not.toBeInTheDocument();
  });
});

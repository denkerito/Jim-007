import {afterAll, afterEach, beforeAll, describe, expect, it} from "vitest";
import {cleanup, render, screen} from "@testing-library/react";
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
    );
    renderAt("/");
    expect(await screen.findByRole("heading", {name: "Dashboard"})).toBeInTheDocument();
    expect((await screen.findAllByText("Bench Press")).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", {name: "View all workouts"})).toHaveAttribute("href", "/workouts");
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

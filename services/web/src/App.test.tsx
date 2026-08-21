import {afterAll, afterEach, beforeAll, describe, expect, it} from "vitest";
import {render, screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {MemoryRouter} from "react-router-dom";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {http, HttpResponse} from "msw";
import {setupServer} from "msw/node";
import {App} from "./App";
import {setCsrf} from "./api";

const server = setupServer();
beforeAll(() => server.listen({onUnhandledRequest: "error"}));
afterEach(() => {server.resetHandlers(); setCsrf(null)});
afterAll(() => server.close());

function renderAt(path: string) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}, mutations: {retry: false}}});
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>);
}

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
});

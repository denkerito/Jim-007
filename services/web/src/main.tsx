import {StrictMode} from "react";
import {createRoot} from "react-dom/client";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {BrowserRouter} from "react-router-dom";
import {App} from "./App";
import "./index.css";

export const queryClient = new QueryClient({defaultOptions: {queries: {retry: (count, error) => !("status" in (error as object)) && count < 2, staleTime: 15_000}, mutations: {retry: false}}});
createRoot(document.getElementById("root")!).render(<StrictMode><QueryClientProvider client={queryClient}><BrowserRouter><App /></BrowserRouter></QueryClientProvider></StrictMode>);

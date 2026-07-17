import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./test/msw/server";

// MSW: assert on unhandled requests so a test that forgets a handler fails loudly
// instead of hitting the network. Handlers are reset between tests so per-test
// overrides don't leak.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

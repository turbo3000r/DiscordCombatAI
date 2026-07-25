import { describe, expect, it } from "vitest";
import { idempotencyKeyFor, resetIdempotency } from "./api";
import { getRuntime } from "./runtime";

describe("idempotency key", () => {
  it("retains key across retries for same action/body", () => {
    resetIdempotency();
    const a = idempotencyKeyFor("send", "hello");
    const b = idempotencyKeyFor("send", "hello");
    expect(a).toBe(b);
  });

  it("creates new key when body changes", () => {
    resetIdempotency();
    const a = idempotencyKeyFor("send", "hello");
    const b = idempotencyKeyFor("send", "world");
    expect(a).not.toBe(b);
  });

  it("creates new key when action changes", () => {
    resetIdempotency();
    const a = idempotencyKeyFor("send", "hello");
    const b = idempotencyKeyFor("done_no_feedback", "hello");
    expect(a).not.toBe(b);
  });
});

describe("runtime", () => {
  it("defaults to development local admin", () => {
    const runtime = getRuntime();
    expect(runtime.mode).toBe("development");
    expect(runtime.localAdminOid).toBeTruthy();
  });
});

import { afterEach } from "vitest";
import { forget } from "./useLoad";

// Remembered pages live for the whole session; each test starts with none.
afterEach(() => forget(""));

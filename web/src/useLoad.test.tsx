import { cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { useLoad } from "./useLoad";

const pending: Record<number, (value: string) => void> = {};

function Probe() {
  const [n, setN] = useState(1);
  const { data } = useLoad(() => new Promise<string>((resolve) => (pending[n] = resolve)), [n]);
  return (
    <>
      <button onClick={() => setN(2)}>next</button>
      <p>{data ?? "loading"}</p>
    </>
  );
}

afterEach(cleanup);

describe("useLoad", () => {
  it("ignores an answer that arrives after a newer request", async () => {
    render(<Probe />);
    act(() => screen.getByText("next").click());
    await act(async () => pending[2]("second"));
    await act(async () => pending[1]("first (late)"));
    expect(screen.getByText("second")).toBeTruthy();
  });
});

import { act, cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { forget, useLoad } from "./useLoad";

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

  it("shows a remembered copy straight away when a page is opened again, then refreshes it", async () => {
    let answer = "first";
    let release: () => void = () => {};
    function Page() {
      const { data } = useLoad(
        () => new Promise<string>((resolve) => (release = () => resolve(answer))),
        [],
        "page",
      );
      return <p>{data ?? "loading"}</p>;
    }
    render(<Page />);
    expect(screen.getByText("loading")).toBeTruthy();
    await act(async () => release());
    cleanup();
    // Back to the page: no blank loading, the remembered copy shows while the new one loads.
    answer = "second";
    render(<Page />);
    expect(screen.getByText("first")).toBeTruthy();
    await act(async () => release());
    expect(screen.getByText("second")).toBeTruthy();
    cleanup();
    forget("page");
    render(<Page />);
    expect(screen.getByText("loading")).toBeTruthy();
  });
});

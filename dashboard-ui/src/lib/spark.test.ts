import { describe, expect, it } from "vitest";
import { sparkPath } from "./spark";

describe("sparkPath", () => {
  it("builds a line across the full width and a closed area", () => {
    const { line, area } = sparkPath([1, 2, 3], 120, 30);
    expect(line.startsWith("M0")).toBe(true);
    expect(line).toContain("L120");
    expect(area.endsWith("Z")).toBe(true);
  });
  it("flat and empty series do not blow up", () => {
    expect(sparkPath([5, 5, 5], 120, 30).line).toContain("L");
    expect(sparkPath([], 120, 30)).toEqual({ line: "", area: "" });
  });
});

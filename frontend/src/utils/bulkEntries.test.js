import { describe, expect, it } from "vitest";
import { BULK_MAX, NOTHING_ENTERED, blockIsEmpty, blocksProblem, buildEntries, countLabels, newBlock, rangeExample, totalLabels } from "./bulkEntries.js";

const labels = (text) => ({ ...newBlock("labels"), text });
const range = (over = {}) => ({ ...newBlock("range"), prefix: "A-", start: "1", end: "40", ...over });

describe("building the request", () => {
  it("sends a list as text with one label per line, and a range with numbers as numbers", () => {
    expect(buildEntries([labels("A-1\nA-2"), range({ pad: "2", suffix: "x" })])).toEqual([
      { labels: "A-1\nA-2" },
      { prefix: "A-", start: 1, end: 40, pad: 2, suffix: "x" },
    ]);
  });
  it("leaves out blocks nobody filled in, and treats a blank zero-fill as none", () => {
    expect(buildEntries([labels("   "), newBlock("range"), range({ pad: "" })])).toEqual([
      { prefix: "A-", start: 1, end: 40, pad: 0, suffix: "" },
    ]);
  });
  it("gives each block its own id", () => {
    expect(newBlock().id).not.toBe(newBlock().id);
  });
});

describe("counting", () => {
  it("counts non-blank lines and range members", () => {
    expect(countLabels(labels("A\n\n  \nB\r\nC"))).toBe(3);
    expect(countLabels(range())).toBe(40);
    expect(countLabels(range({ start: "5", end: "5" }))).toBe(1);
    expect(countLabels(range({ start: "5", end: "1" }))).toBe(0);
    expect(countLabels(range({ start: "x" }))).toBe(0);
    expect(totalLabels([labels("A\nB"), range({ end: "3" })])).toBe(5);
  });
  it("knows an empty block", () => {
    expect(blockIsEmpty(labels(" \n "))).toBe(true);
    expect(blockIsEmpty(newBlock("range"))).toBe(true);
    expect(blockIsEmpty(range())).toBe(false);
  });
});

describe("catching mistakes before the server is asked", () => {
  it("accepts usable blocks", () => {
    expect(blocksProblem([labels("A-1")])).toBeNull();
    expect(blocksProblem([range(), range({ prefix: "B-" })])).toBeNull();
  });
  it("asks for something to be entered", () => {
    expect(blocksProblem([labels(""), newBlock("range")])).toBe(NOTHING_ENTERED);
  });
  it("explains each range mistake and names the block when there are several", () => {
    expect(blocksProblem([range({ start: "" })])).toMatch(/whole number/);
    expect(blocksProblem([range({ start: "9", end: "2" })])).toMatch(/ends before it starts/);
    expect(blocksProblem([range({ pad: "11" })])).toMatch(/zero-fill/);
    expect(blocksProblem([labels("A"), range({ start: "9", end: "2" })])).toMatch(/^Block 2: /);
    expect(blocksProblem([range({ start: "9", end: "2" })])).not.toMatch(/Block/);
  });
  it("refuses more than the server allows, and allows exactly that many", () => {
    expect(blocksProblem([range({ start: "1", end: String(BULK_MAX) })])).toBeNull();
    expect(blocksProblem([range({ start: "1", end: String(BULK_MAX + 1) })])).toMatch(/at most 500/);
    expect(blocksProblem([range({ end: "300" }), range({ end: "300" })])).toMatch(/at most 500/);
  });
});

describe("the example under a range", () => {
  it("shows the first and last label with zero-fill", () => {
    expect(rangeExample(range({ pad: "2", end: "40" }))).toBe("A-01 to A-40");
    expect(rangeExample(range({ start: "7", end: "7", suffix: "B" }))).toBe("A-7B");
    expect(rangeExample(range({ start: "", end: "" }))).toBe("");
    expect(rangeExample(range({ start: "9", end: "2" }))).toBe("");
    expect(rangeExample(labels("A"))).toBe("");
  });
});

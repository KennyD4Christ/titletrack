import { describe, expect, it } from "vitest";
import { days, formatDate, formatNaira, formatNairaCompact, plural } from "./format.js";

describe("money", () => {
  it("formats full naira", () => {
    expect(formatNaira("5528950000.00")).toBe("₦5,528,950,000");
    expect(formatNaira(0)).toBe("₦0");
    expect(formatNaira(null)).toBe("₦0");
  });
  it("formats compact naira", () => {
    expect(formatNairaCompact("5528950000.00")).toBe("₦5.53bn");
    expect(formatNairaCompact("18750000")).toBe("₦18.75m");
    expect(formatNairaCompact(250000)).toBe("₦250k");
    expect(formatNairaCompact(900)).toBe("₦900");
    expect(formatNairaCompact("2161920000.00")).toBe("₦2.16bn");
  });
});

describe("dates and words", () => {
  it("formats dates without time zone drift", () => {
    expect(formatDate("2026-10-05")).toBe("5 Oct 2026");
    expect(formatDate(null)).toBe("—");
  });
  it("pluralises", () => {
    expect(plural(1, "buyer")).toBe("1 buyer");
    expect(plural(264, "buyer")).toBe("264 buyers");
    expect(days(1)).toBe("1 day");
    expect(days(188)).toBe("188 days");
    expect(days(null)).toBe("—");
  });
});
